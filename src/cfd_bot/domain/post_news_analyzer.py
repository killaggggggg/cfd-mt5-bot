from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from cfd_bot.domain.models import (
    ALL_FX_CURRENCIES,
    EconomicEvent,
    EventImpactAnalysis,
    NewsImpact,
    NewsSentiment,
    PostNewsReaction,
    PostNewsVerdict,
    SymbolSpec,
)

# Higher reading is BEARISH for currency (labor slack / higher deficit)
NEGATIVE_CURRENCY_KEYWORDS: tuple[str, ...] = (
    "unemployment",
    "jobless claims",
    "initial claims",
    "continuing claims",
    "claimant count",
    "trade deficit",
    "budget deficit",
)

# Higher reading signals hawkish central bank / inflation pressure
INFLATION_RATE_KEYWORDS: tuple[str, ...] = (
    "cpi",
    "pce",
    "ppi",
    "inflation",
    "interest rate",
    "rate decision",
    "cash rate",
    "bank rate",
    "fomc rate",
    "refinancing rate",
    "hourly earnings",
    "wage",
)

# Higher reading is BULLISH for currency (growth, demand, jobs, hawkish rates)
POSITIVE_CURRENCY_KEYWORDS: tuple[str, ...] = (
    "cpi",
    "pce",
    "ppi",
    "inflation",
    "gdp",
    "non-farm",
    "nonfarm",
    "nfp",
    "payroll",
    "employment change",
    "adp",
    "retail sales",
    "pmi",
    "ism",
    "industrial production",
    "durable goods",
    "interest rate",
    "rate decision",
    "cash rate",
    "bank rate",
    "housing starts",
    "building permits",
    "consumer confidence",
    "consumer sentiment",
    "hourly earnings",
    "wage",
    "trade balance",
    "jolts",
)

EQUITY_INDEX_TAGS: tuple[str, ...] = (
    "US30",
    "DJ30",
    "WALLST",
    "NAS100",
    "USTEC",
    "US100",
    "NDX",
    "US500",
    "SPX",
    "SP500",
    "GER40",
    "DE40",
    "DAX",
    "UK100",
    "FTSE",
    "JP225",
    "NIKKEI",
)

INVERSE_USD_COMMODITY_TAGS: tuple[str, ...] = ("XAU", "XAG", "GOLD", "SILVER", "USOIL", "UKOIL", "WTI", "BRENT", "BTC", "ETH")


def parse_economic_number(raw: Optional[str | float | int]) -> Optional[float]:
    """Parses strings like '3.5%', '23.8K', '-1.2M', '1.5B', '54.5' into float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    cleaned = str(raw).strip().replace(",", "")
    if not cleaned or cleaned.lower() in ("null", "none", "n/a", "--"):
        return None

    mult = 1.0
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()
    elif cleaned[-1].upper() == "K":
        mult = 1_000.0
        cleaned = cleaned[:-1].strip()
    elif cleaned[-1].upper() == "M":
        mult = 1_000_000.0
        cleaned = cleaned[:-1].strip()
    elif cleaned[-1].upper() == "B":
        mult = 1_000_000_000.0
        cleaned = cleaned[:-1].strip()

    match = re.search(r"[-+]?\d*\.?\d+", cleaned)
    if not match:
        return None
    return float(match.group(0)) * mult


def classify_indicator_rule(title: str) -> tuple[float, bool, str]:
    """
    Returns (currency_polarity, is_inflation_or_rate, rule_label).

    currency_polarity: +1.0 if Actual > Forecast strengthens the currency, -1.0 if weakens, 0.0 if speech/unknown.
    """
    low = title.lower()
    if any(k in low for k in ("speaks", "speech", "press conference", "minutes", "statement", "testifies")):
        return 0.0, False, "Central Bank Speech / Qualitative"
    if any(k in low for k in NEGATIVE_CURRENCY_KEYWORDS):
        return -1.0, False, "Higher = Weaker Labor / Bearish Currency"
    is_inf = any(k in low for k in INFLATION_RATE_KEYWORDS)
    if any(k in low for k in POSITIVE_CURRENCY_KEYWORDS):
        label = "Higher = Hawkish / Bullish Currency" if is_inf else "Higher = Stronger Growth / Bullish Currency"
        return 1.0, is_inf, label
    return 0.0, False, "General Indicator (No Directional Rule)"


def _infer_fx_pair(symbol_name: str, spec: SymbolSpec) -> Optional[tuple[str, str]]:
    base = spec.currency_base.strip().upper()
    quote = spec.currency_profit.strip().upper()
    if base in ALL_FX_CURRENCIES and quote in ALL_FX_CURRENCIES and base != quote:
        return base, quote
    up = symbol_name.upper()
    found = [c for c in ALL_FX_CURRENCIES if c in up]
    if len(found) == 2:
        idx0 = up.index(found[0])
        idx1 = up.index(found[1])
        return (found[0], found[1]) if idx0 < idx1 else (found[1], found[0])
    return None


def _sentiment_from_score(score: float) -> NewsSentiment:
    if score >= 15.0:
        return NewsSentiment.BULLISH
    if score <= -15.0:
        return NewsSentiment.BEARISH
    return NewsSentiment.NEUTRAL


class PostNewsAnalyzer:
    """
    Evaluates Actual vs Forecast using economic rules and measures M5/M15 chart reaction
    (5-30m post-release) to distinguish genuine Trend Continuation from Reversal Traps.
    """

    def score_event_for_symbol(self, ev: EconomicEvent, spec: SymbolSpec) -> EventImpactAnalysis:
        act_num = parse_economic_number(ev.actual)
        fcst_num = parse_economic_number(ev.forecast)
        prev_num = parse_economic_number(ev.previous)
        polarity, is_inf_rate, rule_label = classify_indicator_rule(ev.title)

        impact_weight = {
            NewsImpact.HIGH: 1.0,
            NewsImpact.MEDIUM: 0.6,
            NewsImpact.LOW: 0.25,
            NewsImpact.HOLIDAY: 0.0,
        }.get(ev.impact, 0.5)

        if act_num is None:
            return EventImpactAnalysis(
                title=ev.title,
                currency=ev.currency,
                impact=ev.impact,
                event_time=ev.event_time,
                source=ev.source,
                actual=ev.actual,
                forecast=ev.forecast,
                previous=ev.previous,
                actual_num=None,
                forecast_num=fcst_num,
                previous_num=prev_num,
                surprise_pct=None,
                currency_score=0.0,
                symbol_score=0.0,
                symbol_bias=NewsSentiment.UNKNOWN,
                rule_label=rule_label,
                explanation_th="ยังไม่มีตัวเลข Actual (รอประกาศข่าวจริง หรือป้อนค่า Actual เพื่อวิเคราะห์)",
            )

        ref = fcst_num if fcst_num is not None else prev_num
        if ref is None or polarity == 0.0:
            return EventImpactAnalysis(
                title=ev.title,
                currency=ev.currency,
                impact=ev.impact,
                event_time=ev.event_time,
                source=ev.source,
                actual=ev.actual,
                forecast=ev.forecast,
                previous=ev.previous,
                actual_num=act_num,
                forecast_num=fcst_num,
                previous_num=prev_num,
                surprise_pct=None,
                currency_score=0.0,
                symbol_score=0.0,
                symbol_bias=NewsSentiment.NEUTRAL,
                rule_label=rule_label,
                explanation_th="ข่าวเชิงข้อความหรือไม่มีค่าอ้างอิง Forecast/Previous สำหรับเทียบตัวเลข",
            )

        # Compute normalized surprise percentage
        if abs(ref) > 1e-6:
            surprise_ratio = (act_num - ref) / abs(ref)
        else:
            surprise_ratio = act_num - ref
        surprise_pct = surprise_ratio * 100.0

        # Clamp surprise magnitude so +/-50% surprise saturates around 100 before impact weighting
        raw_ccy_score = max(-100.0, min(100.0, surprise_ratio * 150.0)) * polarity * impact_weight

        # Secondary check vs Previous
        if prev_num is not None and fcst_num is not None:
            prev_dir = 1.0 if act_num > prev_num else (-1.0 if act_num < prev_num else 0.0)
            if prev_dir * polarity * raw_ccy_score > 0:
                raw_ccy_score = max(-100.0, min(100.0, raw_ccy_score * 1.1))

        symbol_score, mapping_note = self._map_currency_score_to_symbol(
            news_currency=ev.currency,
            ccy_score=raw_ccy_score,
            is_inf_rate=is_inf_rate,
            spec=spec,
        )
        bias = _sentiment_from_score(symbol_score)

        cmp_word = "สูงกว่าคาด" if act_num > ref else ("ต่ำกว่าคาด" if act_num < ref else "ตามคาด")
        ccy_dir_word = "บวก" if raw_ccy_score > 5 else ("ลบ" if raw_ccy_score < -5 else "กลาง")
        sym_dir_word = (
            "หนุนขาขึ้น (Bullish)"
            if bias == NewsSentiment.BULLISH
            else ("กดดันขาลง (Bearish)" if bias == NewsSentiment.BEARISH else "เป็นกลาง")
        )
        explanation_th = (
            f"{ev.title} ({ev.currency}) ออกมา {cmp_word} ({ev.actual} vs คาด {ev.forecast or ev.previous}) "
            f"→ ผลต่อ {ev.currency} เป็น{ccy_dir_word} ({raw_ccy_score:+.0f}) → {mapping_note}: {sym_dir_word} ต่อ {spec.name}"
        )

        return EventImpactAnalysis(
            title=ev.title,
            currency=ev.currency,
            impact=ev.impact,
            event_time=ev.event_time,
            source=ev.source,
            actual=ev.actual,
            forecast=ev.forecast,
            previous=ev.previous,
            actual_num=act_num,
            forecast_num=fcst_num,
            previous_num=prev_num,
            surprise_pct=round(surprise_pct, 2),
            currency_score=round(raw_ccy_score, 1),
            symbol_score=round(symbol_score, 1),
            symbol_bias=bias,
            rule_label=rule_label,
            explanation_th=explanation_th,
        )

    def _map_currency_score_to_symbol(
        self,
        news_currency: str,
        ccy_score: float,
        is_inf_rate: bool,
        spec: SymbolSpec,
    ) -> tuple[float, str]:
        name_up = spec.name.upper()
        ccy = news_currency.upper()

        # 1. FX Pairs (base/quote)
        fx_pair = _infer_fx_pair(name_up, spec)
        if fx_pair is not None:
            base, quote = fx_pair
            if ccy == base:
                return ccy_score, f"{ccy} เป็นสกุลฐานของคู่เงิน"
            if ccy == quote:
                return -ccy_score, f"{ccy} เป็นสกุลอ้างอิง (ผกผัน)"
            return 0.0, f"{ccy} ไม่ใช่สกุลเงินในคู่ {spec.name}"

        # 2. Equity Indices (US30, NAS100, US500, GER40, UK100, JP225)
        if any(tag in name_up for tag in EQUITY_INDEX_TAGS):
            if is_inf_rate:
                # Higher inflation / rates hurt equity valuations
                return -ccy_score, "ข่าวเงินเฟ้อ/ดอกเบี้ยสูงกดดันตลาดหุ้น"
            # Growth / labor strength (polarity * score already positive when strong growth, negative when unemployment spikes)
            return ccy_score * 0.8, "ข่าวเศรษฐกิจ/การจ้างงานส่งผลทิศทางเดียวกับดัชนีหุ้น"

        # 3. Gold, Silver, Oil, Crypto priced in USD
        if any(tag in name_up for tag in INVERSE_USD_COMMODITY_TAGS):
            if ccy == "USD":
                return -ccy_score, "ดอลลาร์แข็งกดดันราคาทอง/สินค้าโภคภัณฑ์ (ผกผัน)"
            return 0.0, f"ผลกระทบทางอ้อมต่อ {spec.name}"

        # Fallback: check currency_profit
        if ccy == spec.currency_profit.upper():
            return -ccy_score, f"{ccy} เป็นสกุลราคาอ้างอิงของ {spec.name}"
        if ccy == spec.currency_base.upper():
            return ccy_score, f"{ccy} เป็นสกุลฐานของ {spec.name}"
        return 0.0, f"{ccy} ไม่ได้ผูกตรงกับ {spec.name}"

    def analyze_post_news_reaction(
        self,
        event_analysis: EventImpactAnalysis,
        spec: SymbolSpec,
        m5_rates: pd.DataFrame,
        m15_rates: pd.DataFrame,
        now: Optional[datetime] = None,
    ) -> PostNewsReaction:
        """
        Inspects M5 and M15 price behavior after the event timestamp to classify
        whether the market is genuinely following the news or staging a reversal trap.
        """
        utc_now = now or datetime.now(timezone.utc)
        if utc_now.tzinfo is None:
            utc_now = utc_now.replace(tzinfo=timezone.utc)

        ev_time = event_analysis.event_time
        mins_since = (utc_now - ev_time).total_seconds() / 60.0
        point = spec.point if spec.point > 0 else 0.0001

        if mins_since < 0:
            return PostNewsReaction(
                event_title=event_analysis.title,
                event_currency=event_analysis.currency,
                event_time=ev_time,
                minutes_since_release=round(mins_since, 1),
                symbol=spec.name,
                fundamental_bias=event_analysis.symbol_bias,
                fundamental_score=event_analysis.symbol_score,
                verdict=PostNewsVerdict.PRE_NEWS,
                confidence_score=0.0,
                pre_close=spec.bid,
                latest_price=spec.bid,
                net_move_points=0.0,
                pre_atr_m5=0.0,
                impulse_atr_ratio=0.0,
                max_up_atr_ratio=0.0,
                max_down_atr_ratio=0.0,
                m5_direction="FLAT",
                m15_direction="FLAT",
                spread_normalized=True,
                wick_trap_detected=False,
                aligned_with_news=None,
                summary_th=f"ยังไม่ถึงเวลาประกาศข่าว (อีก {abs(mins_since):.0f} นาที)",
            )

        if m5_rates.empty or len(m5_rates) < 15:
            return self._insufficient_data_reaction(event_analysis, spec, mins_since)

        df5 = m5_rates.copy()
        df5["time"] = pd.to_datetime(df5["time"], utc=True)
        # Align UTC event timestamp to broker bar timestamps when broker clock differs from UTC
        ev_bar_time = ev_time + timedelta(seconds=spec.server_utc_offset_seconds or 0)
        pre_bars = df5[df5["time"] < ev_bar_time]
        post_bars = df5[df5["time"] >= ev_bar_time]

        if len(pre_bars) < 5 or post_bars.empty:
            return self._insufficient_data_reaction(event_analysis, spec, mins_since)

        pre_atr_m5 = self._compute_atr(pre_bars.tail(20), period=14)
        if pre_atr_m5 is None or pre_atr_m5 <= 0:
            pre_atr_m5 = max(point * 10.0, float((pre_bars["high"] - pre_bars["low"]).mean()))

        pre_close = float(pre_bars.iloc[-1]["close"])
        latest_price = float(post_bars.iloc[-1]["close"])
        post_high = float(post_bars["high"].max())
        post_low = float(post_bars["low"].min())

        net_move = latest_price - pre_close
        net_move_points = net_move / point
        impulse_atr = abs(net_move) / pre_atr_m5
        max_up_atr = max(0.0, post_high - pre_close) / pre_atr_m5
        max_down_atr = max(0.0, pre_close - post_low) / pre_atr_m5

        m5_dir = "UP" if net_move > 0.25 * pre_atr_m5 else ("DOWN" if net_move < -0.25 * pre_atr_m5 else "FLAT")
        m15_dir = self._compute_m15_direction(m15_rates, ev_bar_time, pre_close, pre_atr_m5)
        spread_normalized = (spec.spread_price / pre_atr_m5) <= 0.35 if pre_atr_m5 > 0 else True

        # Detect classic spike-and-collapse wick traps
        upper_trap = max_up_atr >= 1.2 and net_move < (0.35 * (post_high - pre_close))
        lower_trap = max_down_atr >= 1.2 and (-net_move) < (0.35 * (pre_close - post_low))
        wick_trap = upper_trap or lower_trap

        # Evaluate alignment with Fundamental Bias (if Actual exists)
        fund_bias = event_analysis.symbol_bias
        aligned: Optional[bool] = None
        if fund_bias == NewsSentiment.BULLISH:
            aligned = m5_dir == "UP"
        elif fund_bias == NewsSentiment.BEARISH:
            aligned = m5_dir == "DOWN"

        # 1. Immediate shock phase (< 5 mins or spread still blown out)
        if mins_since < 5.0 or not spread_normalized:
            wait_reason = "ยังไม่ครบ 5 นาทีหลังข่าวออก" if mins_since < 5.0 else "Spread หลังข่าวยังถ่างเกินปกติ"
            return PostNewsReaction(
                event_title=event_analysis.title,
                event_currency=event_analysis.currency,
                event_time=ev_time,
                minutes_since_release=round(mins_since, 1),
                symbol=spec.name,
                fundamental_bias=fund_bias,
                fundamental_score=event_analysis.symbol_score,
                verdict=PostNewsVerdict.WAIT_SETTLE,
                confidence_score=25.0,
                pre_close=pre_close,
                latest_price=latest_price,
                net_move_points=round(net_move_points, 1),
                pre_atr_m5=pre_atr_m5,
                impulse_atr_ratio=round(impulse_atr, 2),
                max_up_atr_ratio=round(max_up_atr, 2),
                max_down_atr_ratio=round(max_down_atr, 2),
                m5_direction=m5_dir,
                m15_direction=m15_dir,
                spread_normalized=spread_normalized,
                wick_trap_detected=wick_trap,
                aligned_with_news=aligned,
                summary_th=f"รอตลาดนิ่ง ({wait_reason}) • ขยับสุทธิ {net_move_points:+.1f} pts ({impulse_atr:.2f}×ATR)",
            )

        # 2. Trap / Reversal Detection (wick snapback OR strong price move opposite to news bias)
        conflict_reversal = (aligned is False) and impulse_atr >= 0.8
        if upper_trap or (conflict_reversal and m5_dir == "DOWN"):
            conf = min(95.0, 55.0 + max_up_atr * 15.0 + (15.0 if m15_dir == "DOWN" else 0.0))
            reason_tag = "พุ่งขึ้นกวาดไส้บนแล้วเทกลับ" if upper_trap else "กราฟวิ่งลงสวนทางกับผลข่าว"
            return PostNewsReaction(
                event_title=event_analysis.title,
                event_currency=event_analysis.currency,
                event_time=ev_time,
                minutes_since_release=round(mins_since, 1),
                symbol=spec.name,
                fundamental_bias=fund_bias,
                fundamental_score=event_analysis.symbol_score,
                verdict=PostNewsVerdict.REVERSAL_TRAP_BEARISH,
                confidence_score=round(conf, 1),
                pre_close=pre_close,
                latest_price=latest_price,
                net_move_points=round(net_move_points, 1),
                pre_atr_m5=pre_atr_m5,
                impulse_atr_ratio=round(impulse_atr, 2),
                max_up_atr_ratio=round(max_up_atr, 2),
                max_down_atr_ratio=round(max_down_atr, 2),
                m5_direction=m5_dir,
                m15_direction=m15_dir,
                spread_normalized=spread_normalized,
                wick_trap_detected=True,
                aligned_with_news=aligned,
                summary_th=f"ระวังกับดักหักมุมลง ({reason_tag}) • M5={m5_dir}, M15={m15_dir}, ขึ้นสุด {max_up_atr:.2f}×ATR ก่อนย่อ",
            )

        if lower_trap or (conflict_reversal and m5_dir == "UP"):
            conf = min(95.0, 55.0 + max_down_atr * 15.0 + (15.0 if m15_dir == "UP" else 0.0))
            reason_tag = "ทุบลงกวาดไส้ล่างแล้วดีดกลับ" if lower_trap else "กราฟดีดขึ้นสวนทางกับผลข่าว"
            return PostNewsReaction(
                event_title=event_analysis.title,
                event_currency=event_analysis.currency,
                event_time=ev_time,
                minutes_since_release=round(mins_since, 1),
                symbol=spec.name,
                fundamental_bias=fund_bias,
                fundamental_score=event_analysis.symbol_score,
                verdict=PostNewsVerdict.REVERSAL_TRAP_BULLISH,
                confidence_score=round(conf, 1),
                pre_close=pre_close,
                latest_price=latest_price,
                net_move_points=round(net_move_points, 1),
                pre_atr_m5=pre_atr_m5,
                impulse_atr_ratio=round(impulse_atr, 2),
                max_up_atr_ratio=round(max_up_atr, 2),
                max_down_atr_ratio=round(max_down_atr, 2),
                m5_direction=m5_dir,
                m15_direction=m15_dir,
                spread_normalized=spread_normalized,
                wick_trap_detected=True,
                aligned_with_news=aligned,
                summary_th=f"ระวังกับดักหักมุมขึ้น ({reason_tag}) • M5={m5_dir}, M15={m15_dir}, ลงสุด {max_down_atr:.2f}×ATR ก่อนดีด",
            )

        # 3. Genuine Trend Continuation
        if impulse_atr >= 0.8 and m5_dir in ("UP", "DOWN") and (m15_dir == m5_dir or m15_dir == "FLAT"):
            verdict = (
                PostNewsVerdict.TREND_CONTINUATION_BULLISH
                if m5_dir == "UP"
                else PostNewsVerdict.TREND_CONTINUATION_BEARISH
            )
            conf = min(95.0, 50.0 + impulse_atr * 15.0 + (15.0 if aligned is True else 0.0) + (10.0 if m15_dir == m5_dir else 0.0))
            align_txt = "สอดคล้องกับผลข่าว" if aligned is True else ("ยังไม่มีตัวเลข Actual แต่แรงกราฟชัด" if aligned is None else "แรงกราฟยืนยันชัด")
            dir_th = "ขาขึ้น" if m5_dir == "UP" else "ขาลง"
            return PostNewsReaction(
                event_title=event_analysis.title,
                event_currency=event_analysis.currency,
                event_time=ev_time,
                minutes_since_release=round(mins_since, 1),
                symbol=spec.name,
                fundamental_bias=fund_bias,
                fundamental_score=event_analysis.symbol_score,
                verdict=verdict,
                confidence_score=round(conf, 1),
                pre_close=pre_close,
                latest_price=latest_price,
                net_move_points=round(net_move_points, 1),
                pre_atr_m5=pre_atr_m5,
                impulse_atr_ratio=round(impulse_atr, 2),
                max_up_atr_ratio=round(max_up_atr, 2),
                max_down_atr_ratio=round(max_down_atr, 2),
                m5_direction=m5_dir,
                m15_direction=m15_dir,
                spread_normalized=spread_normalized,
                wick_trap_detected=False,
                aligned_with_news=aligned,
                summary_th=f"ตลาดวิ่งตามแนวโน้ม{dir_th}จริง ({align_txt}) • ขยับ {net_move_points:+.1f} pts ({impulse_atr:.2f}×ATR), M5+M15 ยืนยัน",
            )

        # 4. Otherwise Choppy / Indecisive
        return PostNewsReaction(
            event_title=event_analysis.title,
            event_currency=event_analysis.currency,
            event_time=ev_time,
            minutes_since_release=round(mins_since, 1),
            symbol=spec.name,
            fundamental_bias=fund_bias,
            fundamental_score=event_analysis.symbol_score,
            verdict=PostNewsVerdict.CHOPPY_RANGE,
            confidence_score=35.0,
            pre_close=pre_close,
            latest_price=latest_price,
            net_move_points=round(net_move_points, 1),
            pre_atr_m5=pre_atr_m5,
            impulse_atr_ratio=round(impulse_atr, 2),
            max_up_atr_ratio=round(max_up_atr, 2),
            max_down_atr_ratio=round(max_down_atr, 2),
            m5_direction=m5_dir,
            m15_direction=m15_dir,
            spread_normalized=spread_normalized,
            wick_trap_detected=False,
            aligned_with_news=aligned,
            summary_th=f"ตลาดยังไม่เลือกทางชัดเจนหลังข่าว (แกว่งสุทธิ {net_move_points:+.1f} pts, {impulse_atr:.2f}×ATR)",
        )

    def _insufficient_data_reaction(
        self,
        event_analysis: EventImpactAnalysis,
        spec: SymbolSpec,
        mins_since: float,
    ) -> PostNewsReaction:
        return PostNewsReaction(
            event_title=event_analysis.title,
            event_currency=event_analysis.currency,
            event_time=event_analysis.event_time,
            minutes_since_release=round(mins_since, 1),
            symbol=spec.name,
            fundamental_bias=event_analysis.symbol_bias,
            fundamental_score=event_analysis.symbol_score,
            verdict=PostNewsVerdict.CHOPPY_RANGE,
            confidence_score=0.0,
            pre_close=spec.bid,
            latest_price=spec.bid,
            net_move_points=0.0,
            pre_atr_m5=0.0,
            impulse_atr_ratio=0.0,
            max_up_atr_ratio=0.0,
            max_down_atr_ratio=0.0,
            m5_direction="FLAT",
            m15_direction="FLAT",
            spread_normalized=True,
            wick_trap_detected=False,
            aligned_with_news=None,
            summary_th="ข้อมูลแท่งเทียน M5 ก่อน/หลังข่าวไม่เพียงพอสำหรับการวิเคราะห์",
        )

    @staticmethod
    def _compute_m15_direction(
        m15_rates: pd.DataFrame,
        ev_time: datetime,
        pre_close: float,
        pre_atr_m5: float,
    ) -> str:
        if m15_rates.empty:
            return "FLAT"
        df15 = m15_rates.copy()
        df15["time"] = pd.to_datetime(df15["time"], utc=True)
        post15 = df15[df15["time"] >= ev_time]
        if post15.empty:
            latest15 = float(df15.iloc[-1]["close"])
        else:
            latest15 = float(post15.iloc[-1]["close"])
        diff = latest15 - pre_close
        if diff > 0.25 * pre_atr_m5:
            return "UP"
        if diff < -0.25 * pre_atr_m5:
            return "DOWN"
        return "FLAT"

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        if len(df) < 2:
            return None
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        prev_close = df["close"].astype(float).shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        win = min(period, len(df))
        val = tr.rolling(window=win).mean().iloc[-1]
        return float(val) if pd.notna(val) and val > 0 else None
