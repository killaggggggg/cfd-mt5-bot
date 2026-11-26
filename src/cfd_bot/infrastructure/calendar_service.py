from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from cfd_bot.domain.models import (
    ALL_FX_CURRENCIES,
    CURRENCY_TO_COUNTRY,
    CalendarCheckResult,
    EconomicEvent,
    NewsImpact,
    SymbolSpec,
    extract_symbol_currencies,
)
from cfd_bot.domain.ports import CalendarPort, PreventivePolicyPort

MAJOR_MARKET_HOLIDAY_KEYWORDS: tuple[str, ...] = (
    "new year",
    "martin luther king",
    "presidents",
    "good friday",
    "easter monday",
    "memorial day",
    "juneteenth",
    "independence day",
    "labour day",
    "labor day",
    "thanksgiving",
    "christmas",
    "boxing day",
    "st. stephen",
    "early may bank holiday",
    "spring bank holiday",
    "summer bank holiday",
)

CalendarServiceProtocol = CalendarPort


def default_mt5_bridge_path() -> Path:
    """Default Common\\Files path where CFDCalendarBridge.mq5 writes live MT5 calendar JSON."""
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / "cfd_calendar_bridge.json"


class EconomicCalendarService:
    """
    Fetches, disk-caches, and evaluates economic releases and bank holidays.

    Sources:
    1. MT5 Built-in Calendar Bridge JSON (live actual/forecast/previous exported by CFDCalendarBridge.mq5)
    2. Weekly economic releases & market-affecting bank holidays (Faireconomy/ForexFactory JSON)
    3. Official national public/bank holidays per country (Nager.Date public API)
    4. Optional local custom events & manual actual overrides (config/custom_events.json & config/news_actuals.json)
    """

    FF_WEEKLY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    NAGER_URL_TEMPLATE = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"

    def __init__(
        self,
        config: PreventivePolicyPort,
        cache_dir: Path | str = "data/cache",
        custom_events_path: Optional[Path | str] = None,
        bridge_path: Optional[Path | str] = None,
        manual_actuals_path: Optional[Path | str] = None,
    ) -> None:
        self._cfg = config
        self._cache_dir = Path(cache_dir)
        self._custom_events_path = Path(custom_events_path) if custom_events_path else (
            Path(config.custom_events_path) if config.custom_events_path else None
        )
        self._bridge_path = Path(bridge_path) if bridge_path else default_mt5_bridge_path()
        if manual_actuals_path is not None:
            self._manual_actuals_path = Path(manual_actuals_path)
        elif self._custom_events_path is not None:
            self._manual_actuals_path = self._custom_events_path.parent / "news_actuals.json"
        else:
            self._manual_actuals_path = Path("config/news_actuals.json")

        self._memory_events: list[EconomicEvent] = []
        self._last_refresh_ts: float = 0.0

    def invalidate_cache(self) -> None:
        self._memory_events = []
        self._last_refresh_ts = 0.0

    def get_bridge_status(self) -> dict[str, Any]:
        """Reports whether MT5 CFDCalendarBridge JSON is present and fresh."""
        if not self._bridge_path.exists():
            return {
                "detected": False,
                "path": str(self._bridge_path),
                "event_count": 0,
                "updated_utc": None,
            }
        try:
            payload = json.loads(self._bridge_path.read_text(encoding="utf-8-sig"))
            events = payload.get("events", []) if isinstance(payload, dict) else []
            return {
                "detected": True,
                "path": str(self._bridge_path),
                "event_count": len(events),
                "updated_utc": payload.get("updated_utc") if isinstance(payload, dict) else None,
            }
        except Exception:
            return {
                "detected": False,
                "path": str(self._bridge_path),
                "event_count": 0,
                "updated_utc": None,
            }

    def save_manual_actual(
        self,
        title: str,
        currency: str,
        event_time_iso: str,
        actual: str,
        forecast: Optional[str] = None,
        previous: Optional[str] = None,
        impact: str = "HIGH",
    ) -> list[dict[str, Any]]:
        """Stores or updates a manual Actual/Forecast override for post-news scoring."""
        self._manual_actuals_path.parent.mkdir(parents=True, exist_ok=True)
        items = self._read_manual_actuals_raw()
        norm_title = title.strip()
        norm_ccy = currency.strip().upper()
        norm_date = event_time_iso.strip()

        updated = False
        for item in items:
            if (
                str(item.get("title", "")).strip().lower() == norm_title.lower()
                and str(item.get("currency", "")).strip().upper() == norm_ccy
                and str(item.get("event_time", ""))[:10] == norm_date[:10]
            ):
                item["actual"] = actual
                if forecast is not None:
                    item["forecast"] = forecast
                if previous is not None:
                    item["previous"] = previous
                updated = True
                break

        if not updated:
            items.append(
                {
                    "title": norm_title,
                    "currency": norm_ccy,
                    "impact": impact.strip().upper(),
                    "event_time": norm_date,
                    "actual": actual,
                    "forecast": forecast,
                    "previous": previous,
                }
            )

        self._manual_actuals_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        self.invalidate_cache()
        return items

    def evaluate(
        self,
        spec: SymbolSpec,
        now: datetime,
        expected_hold_days: float = 0.0,
    ) -> CalendarCheckResult:
        utc_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        utc_now = utc_now.astimezone(timezone.utc)
        currencies = extract_symbol_currencies(spec)

        reasons: list[str] = []
        warnings: list[str] = []

        if not self._cfg.enable_news_guard and not self._cfg.enable_holiday_guard:
            return CalendarCheckResult(blocked=False, reasons=[], warnings=[], upcoming_events=[])

        events, fetch_warnings = self.get_events(currencies=currencies, now=utc_now)
        warnings.extend(fetch_warnings)

        if self._cfg.fail_closed_on_calendar_error and fetch_warnings and not events:
            reasons.append("Economic calendar unavailable and fail_closed_on_calendar_error is enabled")
            return CalendarCheckResult(blocked=True, reasons=reasons, warnings=warnings, upcoming_events=[])

        relevant = [ev for ev in events if ev.currency in currencies or ev.currency == "ALL"]

        upcoming: list[EconomicEvent] = []
        lookahead_end = utc_now + timedelta(hours=self._cfg.calendar_lookahead_hours)

        for ev in relevant:
            if ev.impact == NewsImpact.HOLIDAY:
                if not self._cfg.enable_holiday_guard:
                    continue
                ev_date = date.fromisoformat(ev.local_date) if ev.local_date else ev.event_time.date()
                today_utc = utc_now.date()
                lookahead_date = (utc_now + timedelta(hours=self._cfg.calendar_lookahead_hours)).date()
                hold_end_date = (
                    today_utc + timedelta(days=max(1, int(expected_hold_days + 0.99)))
                    if expected_hold_days > 0.0
                    else today_utc
                )
                check_until = max(lookahead_date, hold_end_date)

                if ev_date == today_utc:
                    upcoming.append(ev)
                    if ev.is_major_market_holiday:
                        msg = f"Bank Holiday today ({ev.currency}): {ev.title}"
                        if self._cfg.holiday_policy == "block":
                            reasons.append(msg)
                        else:
                            warnings.append(msg)
                    else:
                        warnings.append(f"Minor bank holiday today ({ev.currency}): {ev.title}")
                elif today_utc < ev_date <= check_until:
                    upcoming.append(ev)
                    if ev.is_major_market_holiday:
                        prefix = (
                            "Upcoming holiday during hold"
                            if expected_hold_days > 0.0 and ev_date <= hold_end_date
                            else "Upcoming Bank Holiday"
                        )
                        warnings.append(f"{prefix} ({ev_date.isoformat()} {ev.currency}): {ev.title}")
                continue

            if (
                ev.impact in (NewsImpact.HIGH, NewsImpact.MEDIUM)
                and ev.event_time >= utc_now - timedelta(minutes=max(int(self._cfg.news_block_minutes_after or 0), 60))
                and ev.event_time <= lookahead_end
            ):
                upcoming.append(ev)

            if not self._cfg.enable_news_guard:
                continue

            should_block_tier = (
                ev.impact == NewsImpact.HIGH
                or (ev.impact == NewsImpact.MEDIUM and self._cfg.block_medium_impact_news)
            )
            if not should_block_tier:
                continue

            if ev.impact == NewsImpact.HIGH:
                raw_before = self._cfg.news_block_minutes_before
                raw_after = self._cfg.news_block_minutes_after
            else:
                raw_before = self._cfg.news_medium_block_minutes_before
                raw_after = self._cfg.news_medium_block_minutes_after

            before_min = int(raw_before or 0)
            after_min = int(raw_after or 0)

            in_blackout = False
            if (raw_before is not None or raw_after is not None) and (before_min > 0 or after_min > 0):
                win_start = ev.event_time - timedelta(minutes=before_min)
                win_end = ev.event_time + timedelta(minutes=after_min)
                in_blackout = win_start <= utc_now <= win_end

            if in_blackout:
                diff_min = int(round((ev.event_time - utc_now).total_seconds() / 60.0))
                rel = f"in {diff_min}m" if diff_min >= 0 else f"{abs(diff_min)}m ago"
                reasons.append(f"{ev.impact.value} news blackout ({ev.currency} {rel}): {ev.title}")
            elif (
                self._cfg.news_warn_hours is not None
                and self._cfg.news_warn_hours > 0
                and utc_now < ev.event_time <= (utc_now + timedelta(hours=self._cfg.news_warn_hours))
            ):
                mins_until = int(round((ev.event_time - utc_now).total_seconds() / 60.0))
                warnings.append(
                    f"Upcoming {ev.impact.value} news in {mins_until // 60}h{mins_until % 60:02d}m ({ev.currency}): {ev.title}"
                )

        dedup_upcoming = self._dedup_events(upcoming)
        return CalendarCheckResult(
            blocked=len(reasons) > 0,
            reasons=list(dict.fromkeys(reasons)),
            warnings=list(dict.fromkeys(warnings)),
            upcoming_events=dedup_upcoming,
        )

    def get_events(self, currencies: set[str], now: datetime) -> tuple[list[EconomicEvent], list[str]]:
        """Loads events from memory/disk cache, MT5 bridge, manual actuals, and remote feeds."""
        warnings: list[str] = []
        mono_now = time.monotonic()
        if self._memory_events and (mono_now - self._last_refresh_ts) < 30.0:
            return self._memory_events, warnings

        events: list[EconomicEvent] = []

        # 1. MT5 Built-in Calendar Bridge (provides actual + forecast + previous when Service is running)
        if self._bridge_path.exists():
            try:
                bridge_raw = json.loads(self._bridge_path.read_text(encoding="utf-8-sig"))
                if isinstance(bridge_raw, dict) and isinstance(bridge_raw.get("events"), list):
                    events.extend(self.parse_mt5_bridge_events(bridge_raw["events"]))
            except Exception as exc:
                warnings.append(f"MT5 Calendar Bridge parse error: {exc}")

        # 2. Weekly economic releases feed (forecast + previous + bank holidays)
        ff_raw, ff_warn = self._load_json_with_cache(
            url=self.FF_WEEKLY_URL,
            cache_file=self._cache_dir / "ff_calendar_thisweek.json",
            ttl_seconds=self._cfg.calendar_cache_ttl_seconds,
            label="Weekly news feed",
        )
        if ff_warn:
            warnings.append(ff_warn)
        if isinstance(ff_raw, list):
            events.extend(self.parse_forexfactory_events(ff_raw))

        # 3. National public/bank holidays per mapped country
        countries = sorted({CURRENCY_TO_COUNTRY[c] for c in currencies if c in CURRENCY_TO_COUNTRY})
        years = sorted({now.year, (now + timedelta(days=14)).year})
        for country in countries:
            currency = next((k for k, v in CURRENCY_TO_COUNTRY.items() if v == country), country)
            for year in years:
                hol_raw, hol_warn = self._load_json_with_cache(
                    url=self.NAGER_URL_TEMPLATE.format(year=year, country=country),
                    cache_file=self._cache_dir / f"holidays_{year}_{country}.json",
                    ttl_seconds=self._cfg.holiday_cache_ttl_seconds,
                    label=f"Holiday feed ({country} {year})",
                )
                if hol_warn:
                    warnings.append(hol_warn)
                if isinstance(hol_raw, list):
                    events.extend(self.parse_nager_holidays(hol_raw, currency=currency))

        # 4. Custom local user events (optional)
        if self._custom_events_path and self._custom_events_path.exists():
            try:
                custom_raw = json.loads(self._custom_events_path.read_text(encoding="utf-8"))
                if isinstance(custom_raw, list):
                    events.extend(self.parse_custom_events(custom_raw))
            except Exception as exc:
                warnings.append(f"Failed to parse custom events file: {exc}")

        # 5. Merge & enrich with manual actual/forecast overrides
        events = self._merge_and_apply_manual_actuals(events)

        self._memory_events = events
        self._last_refresh_ts = mono_now
        return events, warnings

    def _read_manual_actuals_raw(self) -> list[dict[str, Any]]:
        if not self._manual_actuals_path.exists():
            return []
        try:
            data = json.loads(self._manual_actuals_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _merge_and_apply_manual_actuals(self, events: list[EconomicEvent]) -> list[EconomicEvent]:
        # Deduplicate across MT5 bridge & FF weekly feed, preferring entries with non-null actuals
        merged: dict[tuple[str, str, str], EconomicEvent] = {}
        for ev in events:
            day_key = ev.local_date or ev.event_time.date().isoformat()
            key = (day_key, ev.currency, ev.title.lower())
            existing = merged.get(key)
            if existing is None:
                merged[key] = ev
            else:
                merged[key] = EconomicEvent(
                    title=existing.title,
                    currency=existing.currency,
                    impact=existing.impact,
                    event_time=existing.event_time if existing.source == "mt5_bridge" else ev.event_time,
                    is_all_day_holiday=existing.is_all_day_holiday or ev.is_all_day_holiday,
                    is_major_market_holiday=existing.is_major_market_holiday or ev.is_major_market_holiday,
                    local_date=existing.local_date or ev.local_date,
                    source="mt5_bridge" if "mt5_bridge" in (existing.source, ev.source) else existing.source,
                    actual=existing.actual if existing.actual is not None else ev.actual,
                    forecast=existing.forecast if existing.forecast is not None else ev.forecast,
                    previous=existing.previous if existing.previous is not None else ev.previous,
                )

        # Apply manual overrides from config/news_actuals.json
        for item in self._read_manual_actuals_raw():
            title = str(item.get("title", "")).strip()
            currency = str(item.get("currency", "")).strip().upper()
            raw_time = str(item.get("event_time", "")).strip()
            if not title or not currency or not raw_time:
                continue
            day_key = raw_time[:10]
            key = (day_key, currency, title.lower())
            actual_val = str(item["actual"]) if item.get("actual") not in (None, "") else None
            fcst_val = str(item["forecast"]) if item.get("forecast") not in (None, "") else None
            prev_val = str(item["previous"]) if item.get("previous") not in (None, "") else None

            if key in merged:
                base = merged[key]
                merged[key] = EconomicEvent(
                    title=base.title,
                    currency=base.currency,
                    impact=base.impact,
                    event_time=base.event_time,
                    is_all_day_holiday=base.is_all_day_holiday,
                    is_major_market_holiday=base.is_major_market_holiday,
                    local_date=base.local_date,
                    source=f"{base.source}+manual",
                    actual=actual_val if actual_val is not None else base.actual,
                    forecast=fcst_val if fcst_val is not None else base.forecast,
                    previous=prev_val if prev_val is not None else base.previous,
                )
            else:
                try:
                    dt = datetime.fromisoformat(raw_time)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dt_utc = dt.astimezone(timezone.utc)
                    impact = NewsImpact(str(item.get("impact", "HIGH")).upper())
                except ValueError:
                    continue
                merged[key] = EconomicEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_time=dt_utc,
                    local_date=day_key,
                    source="manual",
                    actual=actual_val,
                    forecast=fcst_val,
                    previous=prev_val,
                )

        return list(merged.values())

    @staticmethod
    def _dedup_events(events: list[EconomicEvent]) -> list[EconomicEvent]:
        seen_keys: set[tuple[str, str, str]] = set()
        dedup: list[EconomicEvent] = []
        for ev in sorted(events, key=lambda x: x.event_time):
            key = (ev.local_date or ev.event_time.isoformat(), ev.currency, ev.title.lower())
            if key not in seen_keys:
                seen_keys.add(key)
                dedup.append(ev)
        return dedup

    @staticmethod
    def parse_mt5_bridge_events(raw_items: list[dict[str, Any]]) -> list[EconomicEvent]:
        parsed: list[EconomicEvent] = []
        for item in raw_items:
            title = str(item.get("title", "")).strip()
            currency = str(item.get("currency", "")).strip().upper()
            raw_impact = str(item.get("impact", "LOW")).strip().upper()
            raw_dt = item.get("time_utc")
            if not title or not currency or not raw_dt:
                continue
            try:
                impact = NewsImpact(raw_impact)
            except ValueError:
                impact = NewsImpact.LOW
            try:
                dt = datetime.fromisoformat(str(raw_dt))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_utc = dt.astimezone(timezone.utc)
            except ValueError:
                continue

            def _fmt(v: Any) -> Optional[str]:
                if v is None or v == "":
                    return None
                return str(v)

            is_holiday = impact == NewsImpact.HOLIDAY
            parsed.append(
                EconomicEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_time=dt_utc,
                    is_all_day_holiday=is_holiday,
                    is_major_market_holiday=is_holiday,
                    local_date=dt_utc.date().isoformat(),
                    source="mt5_bridge",
                    actual=_fmt(item.get("actual")),
                    forecast=_fmt(item.get("forecast")),
                    previous=_fmt(item.get("previous")),
                )
            )
        return parsed

    @staticmethod
    def parse_forexfactory_events(raw_items: list[dict[str, Any]]) -> list[EconomicEvent]:
        parsed: list[EconomicEvent] = []
        impact_map = {
            "HIGH": NewsImpact.HIGH,
            "MEDIUM": NewsImpact.MEDIUM,
            "LOW": NewsImpact.LOW,
            "HOLIDAY": NewsImpact.HOLIDAY,
        }
        for item in raw_items:
            raw_impact = str(item.get("impact", "")).strip().upper()
            impact = impact_map.get(raw_impact)
            raw_date = item.get("date")
            title = str(item.get("title", "")).strip()
            currency = str(item.get("country", "")).strip().upper()
            if impact is None or not raw_date or not title or not currency:
                continue
            try:
                dt = datetime.fromisoformat(str(raw_date))
                local_d = dt.date().isoformat()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_utc = dt.astimezone(timezone.utc)
            except ValueError:
                continue

            is_holiday = impact == NewsImpact.HOLIDAY
            fcst = str(item.get("forecast", "")).strip() or None
            prev = str(item.get("previous", "")).strip() or None
            act = str(item.get("actual", "")).strip() or None
            parsed.append(
                EconomicEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_time=dt_utc,
                    is_all_day_holiday=is_holiday,
                    is_major_market_holiday=is_holiday,
                    local_date=local_d,
                    source="forexfactory",
                    actual=act,
                    forecast=fcst,
                    previous=prev,
                )
            )
        return parsed

    @staticmethod
    def parse_nager_holidays(raw_items: list[dict[str, Any]], currency: str) -> list[EconomicEvent]:
        parsed: list[EconomicEvent] = []
        for item in raw_items:
            raw_date = item.get("date")
            name = str(item.get("name") or item.get("localName") or "").strip()
            is_global = bool(item.get("global", False))
            types = [str(t) for t in (item.get("types") or [])]
            if not raw_date or not name or not is_global:
                continue
            if not any(t in ("Public", "Bank") for t in types):
                continue

            try:
                d = date.fromisoformat(str(raw_date))
            except ValueError:
                continue

            name_lower = name.lower()
            is_major = any(kw in name_lower for kw in MAJOR_MARKET_HOLIDAY_KEYWORDS)
            parsed.append(
                EconomicEvent(
                    title=name,
                    currency=currency,
                    impact=NewsImpact.HOLIDAY,
                    event_time=datetime(d.year, d.month, d.day, 0, 0, tzinfo=timezone.utc),
                    is_all_day_holiday=True,
                    is_major_market_holiday=is_major,
                    local_date=d.isoformat(),
                    source="nager",
                )
            )
        return parsed

    @staticmethod
    def parse_custom_events(raw_items: list[dict[str, Any]]) -> list[EconomicEvent]:
        parsed: list[EconomicEvent] = []
        for item in raw_items:
            title = str(item.get("title", "")).strip()
            currency = str(item.get("currency", "ALL")).strip().upper()
            raw_impact = str(item.get("impact", "HIGH")).strip().upper()
            raw_dt = item.get("date") or item.get("datetime")
            if not title or not raw_dt:
                continue
            try:
                impact = NewsImpact(raw_impact)
            except ValueError:
                impact = NewsImpact.HIGH

            try:
                raw_str = str(raw_dt)
                if len(raw_str) == 10:
                    d = date.fromisoformat(raw_str)
                    dt_utc = datetime(d.year, d.month, d.day, 0, 0, tzinfo=timezone.utc)
                    local_d = d.isoformat()
                    all_day = True
                else:
                    dt = datetime.fromisoformat(raw_str)
                    local_d = dt.date().isoformat()
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dt_utc = dt.astimezone(timezone.utc)
                    all_day = impact == NewsImpact.HOLIDAY
            except ValueError:
                continue

            parsed.append(
                EconomicEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_time=dt_utc,
                    is_all_day_holiday=all_day,
                    is_major_market_holiday=bool(item.get("major", True)) if impact == NewsImpact.HOLIDAY else False,
                    local_date=local_d,
                    source="custom",
                    actual=str(item["actual"]) if item.get("actual") not in (None, "") else None,
                    forecast=str(item["forecast"]) if item.get("forecast") not in (None, "") else None,
                    previous=str(item["previous"]) if item.get("previous") not in (None, "") else None,
                )
            )
        return parsed

    def _load_json_with_cache(
        self,
        url: str,
        cache_file: Path,
        ttl_seconds: int,
        label: str,
    ) -> tuple[Any, Optional[str]]:
        now_epoch = time.time()
        if cache_file.exists():
            age = now_epoch - cache_file.stat().st_mtime
            if age <= ttl_seconds:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8")), None
                except Exception:
                    pass

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (CFD-MT5-Bot)"})
            with urllib.request.urlopen(req, timeout=self._cfg.calendar_http_timeout_seconds) as resp:
                raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode("utf-8"))
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(raw_bytes)
            return data, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    return data, f"{label} refresh failed ({exc}); using cached data"
                except Exception:
                    pass
            return None, f"{label} unreachable ({exc}); calendar guard degraded"
