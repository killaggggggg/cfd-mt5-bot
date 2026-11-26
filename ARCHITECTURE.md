# ARCHITECTURE.md — CFD MT5 Bot & Operation Center

เอกสารฉบับนี้เป็น **Single Source of Truth** สำหรับควบคุมโครงสร้างโค้ด, ขอบเขตระหว่างชั้น (Layer Boundaries), และการออกแบบหน้าจอ (UI/UX) ของโปรเจกต์ `cfd-mt5-bot`

---

## 1. หลักการ Clean Architecture (Dependency Rule)

ทิศทางการพึ่งพา (Import Direction) ต้องชี้ **เข้าด้านในเสมอ** ห้ามชั้นในสุดอ้างอิงชั้นนอกเด็ดขาด:

```text
[ Infrastructure / Presentation ]  --->  [ Application ]  --->  [ Domain ]
   (MT5, HTTP, Disk, Starlette, UI)        (Use Cases)        (Entities, Ports, Math)
```

### ชั้นที่ 1: `src/cfd_bot/domain/` (แกนธุรกิจบริสุทธิ์)
* **หน้าที่:** โมเดลข้อมูล, กติกาความเสี่ยง, คณิตศาสตร์การเงิน, กฎเศรษฐศาสตร์, และ Interfaces (`ports.py`)
* **ไฟล์:**
  * `models.py` — Value Objects & Entities (`SymbolSpec`, `AccountState`, `TradeSignal`, `LotPlan`, `PreventiveDecision`, `EconomicEvent`, `PostNewsReaction`)
  * `ports.py` — Abstract Interfaces (`MarketGatewayPort`, `OrderExecutorPort`, `CalendarPort`, `StrategyPort`)
  * `risk_engine.py` — คำนวณ Fixed/Dynamic Lot, Leverage Cap, Margin Budget และ Floor Step
  * `preventive_guard.py` — ตรวจ Spread, Swap (รวม 3x), Commission, Slippage, Net R:R, Rollover, Stops Level, Daily Drawdown
  * `post_news_analyzer.py` — กฎเศรษฐศาสตร์ (`Actual vs Forecast`) และวิเคราะห์แท่งเทียนหลังข่าว M5/M15
  * `strategies/` — Interface `BaseStrategy` และกลยุทธ์ที่ถอดเปลี่ยนได้
* **กฎเหล็ก (ห้ามฝ่าฝืน):**
  * ห้าม `import MetaTrader5`, `starlette`, `uvicorn`, `urllib`, `pydantic` หรือไฟล์ I/O ใน `domain/`
  * ตรวจสอบอัตโนมัติด้วย `tests/test_architecture.py`

### ชั้นที่ 2: `src/cfd_bot/application/` (Use Cases & Orchestration)
* **หน้าที่:** ประสานงานระหว่าง Strategy, PreventiveGuard, RiskEngine และพอร์ตภายนอกตามคำสั่งของผู้ใช้/บอท
* **ไฟล์:**
  * `config.py` — โครงสร้าง Config (`BotConfig`, `RiskConfig`, `PreventiveConfig`, `TradeManagementConfig`) และโหลดค่าจาก `.env`
  * `bot_runner.py` — วงจรการทำงานของบอท 1 รอบ (`evaluate_once`) และลูปการทำงาน (`run_forever`)
  * `operation_service.py` — Use Cases สำหรับหน้าเว็บ (ดูภาพรวม, สแกนโบรกเกอร์, จำลอง Lot/ต้นทุน, วิเคราะห์ข่าว/กราฟหลังข่าว, แก้ไข `.env`, สั่งเริ่ม/หยุดบอท)
* **กฎเหล็ก:**
  * ใช้ Dependencies ผ่าน `domain/ports.py` เท่านั้น ห้าม `import MetaTrader5` หรือ `starlette` โดยตรง

### ชั้นที่ 3: `src/cfd_bot/infrastructure/` (Adapters, External I/O & Web Delivery)
* **หน้าที่:** เชื่อมต่อโลกภายนอกจริง (MetaTrader 5, เว็บปฏิทินข่าว, ไฟล์ Cache/JSON, เว็บเซิร์ฟเวอร์ Starlette)
* **ไฟล์:**
  * `mt5_gateway.py` — Adapter เชื่อม `MetaTrader5` (Auto Symbol Suffix/Alias, Timezone Offset, Auto Commission)
  * `order_executor.py` — Adapter ส่งคำสั่งซื้อ/ขาย/ปิด/บังทุน/Trailing ผ่าน `order_check` + `order_send`
  * `calendar_service.py` — Adapter ดึงและแคชข้อมูลข่าวจาก MT5 Bridge JSON, Weekly Feed, Nager Holidays และไฟล์ผู้ใช้
  * `web/api_server.py` — Thin Starlette HTTP Router + Static SPA Serving
* **Composition Root:** `scripts/` (`run_bot.py`, `start_ops.py`, `inspect_broker.py`, `setup_bot.py`) เป็นจุดประกอบ Infrastructure เข้ากับ Application

### MQL5 Bridge Boundary (`mql5/`)
* `mql5/Services/CFDCalendarBridge.mq5` ทำงานเป็น Service ภายใน MT5 Terminal เพื่อส่งออกตารางข่าวจริง (`Actual/Forecast/Previous`) ไปที่ `Common\Files\cfd_calendar_bridge.json` โดยไม่ผูกกับ Python โดยตรง

---

## 2. Clean Architecture ฝั่ง Frontend (`ui/src/`)

โครงสร้างฝั่ง React แบ่งตาม `flipup-admin-web`:
* `ui/src/domain/` — TypeScript types & status labels (`types.ts`)
* `ui/src/data/` — REST API repository (`api.ts`)
* `ui/src/presentation/`
  * `components/layout/` — `Sidebar.tsx` (พับ-ขยายได้ + เมนูมือถือ) และ `Header.tsx` (แถบสถานะบนสุด)
  * `components/ui/` — Reusable primitives (`PageHeader.tsx`, `SectionCard.tsx`, `StatCard.tsx`, `StatusBadge.tsx`)
  * `pages/` — หน้าหลักแต่ละเมนู (`OverviewPage.tsx`, `SimulatorPage.tsx`, `NewsIntelPage.tsx`, `CalendarPage.tsx`, `SettingsPage.tsx`, `LogsPage.tsx`)

---

## 3. กฎการออกแบบ UI/UX และภาษา (Layout & Simplicity Rules)

1. **โครงสร้างหน้าจอแบบ Sidebar + Header (`flipup-admin-web` Chassis):**
   * ซ้าย: แถบเมนูจัดเป็น 4 หมวดชัดเจน (พับเหลือไอคอนได้บนจอใหญ่, เป็นลิ้นชักบนมือถือ)
   * บน: Header โปร่งใสสูง `56px` แสดงชื่อหน้า, สถานะ MT5, โหมดซ้อม/จริง และปุ่มควบคุมบอท
   * กลาง: พื้นหลัง `#F8FAFC` การ์ดสีขาว `#FFFFFF` มุมโค้ง `16px` (`rounded-2xl`) ขอบบาง `1px solid #E2E8F0`
2. **สีและฟอนต์:**
   * ฟอนต์ **Anuphan** (`300, 400, 600, 700`) ทั่วทั้งโปรเจกต์ พร้อมตัวเลขตรงคอลัมน์ (`tabular-nums`)
   * สีหลัก Action Blue (`#0066CC` / `#0071E3`) — **ห้ามใช้สีม่วงหรือ Gradient ฉูดฉาด**
   * ป้ายสถานะใช้สีโทนอ่อน (`*-50` + ตัวหนังสือ `*-700`) เพื่อความสบายตา
3. **กฎภาษาที่เข้าใจง่าย ("ห้ามใช้คำศัพท์เทคนิคยากเกินจำเป็น"):**
   * ใช้ **ภาษาไทยที่คนทั่วไปอ่านเข้าใจทันที** เป็นหลัก (เช่น "จุดเท่าทุนหลังหักค่าใช้จ่าย", "ปรับขนาดไม้อัตโนมัติ", "กราฟวิ่งตามข่าวจริง", "ระวังกวาดไส้หลอก")
   * วงเล็บคำสั้นๆ เฉพาะคำสากลที่เทรดเดอร์ต้องเห็นตรงกับหน้าจอ MT5 (`Lot`, `SL`, `TP`, `Spread`, `Swap`)
   * ห้ามใช้ Emoji ในระบบ Operation Center ใช้ไอคอนจาก `lucide-react` เท่านั้น

---

## 4. กฎการทดสอบและตรวจสถาปัตยกรรม

ทุกครั้งที่แก้โค้ด ต้องผ่าน:
1. `python -m unittest discover -s tests -v` (รวม `tests/test_architecture.py` ที่ตรวจ Boundary Rule)
2. `cd ui && npm run build` (`tsc -b && vite build` ผ่าน 0 errors)
