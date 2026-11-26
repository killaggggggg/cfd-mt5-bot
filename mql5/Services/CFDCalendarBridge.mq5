//+------------------------------------------------------------------+
//|                                           CFDCalendarBridge.mq5  |
//|            Exports MT5 Built-in Economic Calendar (Actual/Fcst)  |
//|            to Common\Files\cfd_calendar_bridge.json (100% Free)  |
//+------------------------------------------------------------------+
#property service
#property strict

input int InpRefreshSeconds = 30;      // Refresh interval in seconds
input int InpLookbackHours  = 48;      // Past hours to export (captures recent actuals)
input int InpLookaheadHours = 72;      // Future hours to export

string EscapeJson(const string src)
  {
   string out = src;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\r", " ");
   StringReplace(out, "\n", " ");
   return out;
  }

string FormatIsoUtc(const datetime ts)
  {
   // Note: TimeGMT vs TimeTradeServer offset converts MT5 calendar server timestamps cleanly
   datetime offset = TimeTradeServer() - TimeGMT();
   datetime utc_ts = ts - offset;
   MqlDateTime dt;
   TimeToStruct(utc_ts, dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d+00:00",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
  }

string MapImportance(const ENUM_CALENDAR_EVENT_IMPORTANCE imp, const ENUM_CALENDAR_EVENT_TYPE ev_type)
  {
   if(ev_type == CALENDAR_TYPE_HOLIDAY)
      return "HOLIDAY";
   if(imp == CALENDAR_IMPORTANCE_HIGH)
      return "HIGH";
   if(imp == CALENDAR_IMPORTANCE_MODERATE)
      return "MEDIUM";
   return "LOW";
  }

string FormatCalendarNumber(const long raw_value, const string unit)
  {
   if(raw_value == LONG_MIN)
      return "null";
   double scaled = (double)raw_value / 1000000.0;
   return StringFormat("%.6f", scaled);
  }

void ExportCalendarJson()
  {
   datetime now_srv = TimeTradeServer();
   datetime from_dt = now_srv - (InpLookbackHours * 3600);
   datetime to_dt   = now_srv + (InpLookaheadHours * 3600);

   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, from_dt, to_dt);
   if(count < 0)
     {
      Print("CFDCalendarBridge: CalendarValueHistory failed, err=", GetLastError());
      return;
     }

   int handle = FileOpen("cfd_calendar_bridge.json", FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI, 0, CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      Print("CFDCalendarBridge: Failed to open output file, err=", GetLastError());
      return;
     }

   string updated_iso = FormatIsoUtc(now_srv);
   FileWriteString(handle, "{\n");
   FileWriteString(handle, "  \"updated_utc\": \"" + updated_iso + "\",\n");
   FileWriteString(handle, "  \"events\": [\n");

   int written = 0;
   for(int i = 0; i < count; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      MqlCalendarCountry country;
      if(!CalendarCountryById(ev.country_id, country))
         continue;

      string imp = MapImportance(ev.importance, ev.type);
      string unit_str = EnumToString(ev.unit);
      string actual_str = FormatCalendarNumber(values[i].actual_value, unit_str);
      string fcst_str   = FormatCalendarNumber(values[i].forecast_value, unit_str);
      string prev_str   = FormatCalendarNumber(values[i].prev_value, unit_str);

      string entry = StringFormat(
         "    {\"id\": %I64u, \"title\": \"%s\", \"currency\": \"%s\", \"country_code\": \"%s\", "
         "\"impact\": \"%s\", \"time_utc\": \"%s\", \"actual\": %s, \"forecast\": %s, \"previous\": %s, \"unit\": \"%s\"}",
         values[i].id,
         EscapeJson(ev.name),
         EscapeJson(country.currency),
         EscapeJson(country.code),
         imp,
         FormatIsoUtc(values[i].time),
         actual_str,
         fcst_str,
         prev_str,
         EscapeJson(unit_str)
      );

      if(written > 0)
         FileWriteString(handle, ",\n");
      FileWriteString(handle, entry);
      written++;
     }

   FileWriteString(handle, "\n  ]\n}\n");
   FileClose(handle);
  }

void OnStart()
  {
   Print("CFDCalendarBridge Service started. Exporting every ", InpRefreshSeconds, "s to Common\\Files\\cfd_calendar_bridge.json");
   while(!IsStopped())
     {
      ExportCalendarJson();
      Sleep(MathMax(5, InpRefreshSeconds) * 1000);
     }
   Print("CFDCalendarBridge Service stopped.");
  }
//+------------------------------------------------------------------+
