# CFD MT5 Bot (Leverage-Aware & Preventive Cost Guarded)

โครงสร้างบอทเทรด CFD สำหรับ MetaTrader 5 (Python + `MetaTrader5`) ที่แยก **RiskEngine**, **PreventiveGuard**, และ **Strategy** ออกจากกันอย่างชัดเจน

## ความสามารถหลัก
1. **Automated Lot Sizing (Fixed & Dynamic):**
   - `fixed`: ใช้ Lot คงที่ตามกำหนด แต่ยังอยู่ภายใต้การคุม Leverage, Free Margin และ Broker Volume Limits
   - `dynamic_risk`: คำนวณ Lot จาก `% Risk` ของ Equity/Balance โดยรวมระยะ Stop Loss + Commission + Slippage + Projected Swap และปัดเศษลง (**Floor**) ตาม `volume_step` จริงของโบรกเกอร์เสมอ
   - `dynamic_equity_step`: ปรับ Lot เพิ่ม/ลดตามขั้นบันไดของ Equity
2. **Broker Leverage Adaptation (`order_calc_margin`):**
   - ดึง Margin จริงที่โบรกเกอร์เรียกเก็บสำหรับ CFD แต่ละตัว (แยกจาก Account Leverage เช่น บัญชี 1:500 แต่ทองได้ 1:200 หรือดัชนีได้ 1:50)
   - รองรับ **Tiered / Dynamic Leverage** โดยตรวจสอบซ้ำด้วย `order_calc_margin` ที่ขนาด Lot จริงก่อนส่งคำสั่ง
   - กำหนดเพดาน **Max Effective Symbol/Account Leverage**, **Max Margin Usage %**, และ **Min Margin Level %**
3. **Preventive Guard (วิเคราะห์ต้นทุนและความเสี่ยงก่อนเข้าเทรด):**
   - **Spread:** กรอง Max Spread, Spread-to-TP Ratio และ Spread/ATR Spike
   - **Swap:** คำนวณครบทุก MT5 Swap Mode (`POINTS`, `CURRENCY_SYMBOL`, `CURRENCY_MARGIN`, `CURRENCY_DEPOSIT`, `INTEREST_CURRENT/OPEN`) พร้อมตรวจ **Triple Swap Day (3x)** และช่วง **Rollover Blackout**
   - **Commission & Slippage:** คำนวณ **True Break-Even (points)** และ **Net R:R** หลังหักต้นทุนทั้งหมด
   - **Economic News & Bank Holidays:** ดึงปฏิทินข่าวแรง (FOMC, CPI, NFP ฯลฯ) และวันหยุดธนาคารล่วงหน้า (พร้อมระบบ Cache บนเครื่อง + รองรับไฟล์ข่าว/วันหยุดเพิ่มเติมใน `config/custom_events.json`) เพื่อเตือนและหยุดเข้าเทรดก่อน-หลังประกาศข่าวและช่วงวันหยุดตลาด
   - **Market & Safety Guards:** ตรวจ `trade_mode`, `trade_stops_level`, Quote Freshness (กันตลาดปิด/ค้าง) และ **Daily Drawdown Circuit Breaker**
4. **Plug-and-Play Strategy Layer:**
   - สืบทอด `BaseStrategy` ใน `src/cfd_bot/strategies/` เพื่อเสียบ Strategy ใหม่ได้ทันที (มาพร้อม `SessionRangeBreakoutStrategy` เป็นตัวตั้งต้นสำหรับทดสอบ Pipeline)

## การเริ่มต้นใช้งานแบบง่าย (3 ขั้นตอน)

### 1. ตั้งค่าอัตโนมัติจาก MT5 จริง (`setup_bot.py`)
เปิดโปรแกรม MetaTrader 5 และล็อกอินบัญชี Demo/Real ไว้ จากนั้นรัน:
```powershell
python scripts/setup_bot.py
```
สคริปต์จะดึงสเปกจริงจากโบรกเกอร์ (Suffix, ทศนิยม, Margin, Effective Leverage, Timezone, Commission) แล้วสร้างไฟล์ `.env` ให้อัตโนมัติ (ตั้ง `BOT_DRY_RUN=true` ไว้เป็นค่าปลอดภัยเริ่มต้น)

### 2. ตรวจสอบรายละเอียดต้นทุนและขนาด Lot (`inspect_broker.py`)
```powershell
python scripts/inspect_broker.py --symbols XAUUSD EURUSD US30 --side BUY --hold-days 1
```
สคริปต์จะแสดง Account Leverage vs Effective Symbol Leverage จริง, Spread, Swap Mode, วันคิด Swap 3 เท่า, ต้นทุนรวมต่อ 1.0 Lot, True Break-Even และผลคำนวณ Lot ทั้งแบบ Dynamic และ Fixed

### 3. เปิดหน้า Operation Center (React UI) หรือรันบอทผ่าน Terminal
**แบบมีหน้าเว็บ Operation System (แนะนำ — กดดู/คุมทุกอย่างผ่าน Browser):**
```powershell
python scripts/start_ops.py
```
หรือดับเบิลคลิกไฟล์ `start_ops.bat` แล้วเปิดเบราว์เซอร์ไปที่ `http://127.0.0.1:8787`

**แบบรันเฉพาะ Terminal (พร้อม Auto True Break-Even & ATR Trailing Stop):**
```powershell
python scripts/run_bot.py
```

### 4. เปิดใช้งานตัวดึงตัวเลขข่าว Actual จริงจาก MT5 (ฟรี 100%)
ไฟล์ [`CFDCalendarBridge.mq5`](file:///c:/Projects/cfd-mt5-bot/mql5/Services/CFDCalendarBridge.mq5) ถูก Compile เป็น `.ex5` ลงในโฟลเดอร์ `MQL5/Services` ของ MT5 บนเครื่องคุณเรียบร้อยแล้ว:
1. ในโปรแกรม **MetaTrader 5** เปิดหน้าต่าง `Navigator` (`Ctrl+N`)
2. ขยายหัวข้อ `Services` -> คลิกขวาที่ `CFDCalendarBridge` -> กด `Start`
3. ตัวเลข `Actual / Forecast / Previous` จากปฏิทิน MetaQuotes จะถูกส่งเข้าแท็บ **วิเคราะห์ผลข่าว** บน Operation Center อัตโนมัติทุก 30 วินาที

### รัน Unit Tests (Offline)
```powershell
python -m unittest discover -s tests -v
```
