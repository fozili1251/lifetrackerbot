import os
import json
import logging
import re
from groq import Groq
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Google Sheets ──────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_sheet(sheet_name: str):
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(os.environ["SPREADSHEET_ID"])
    return spreadsheet.worksheet(sheet_name)

# ── AI парсинг ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты парсишь сообщения пользователя и определяешь категорию и данные.

Категории:
- finance: расходы, доходы, траты, деньги
- sport: тренировки, пробежки, упражнения, активность
- study: учёба, дедлайны, сдал, задача, зачёт, экзамен
- diet: еда, калории, ккал, приём пищи, съел, выпил

Отвечай СТРОГО в JSON без лишнего текста:

Для finance:
{"category": "finance", "type": "расход/доход", "amount": число, "description": "описание", "date": "YYYY-MM-DD"}

Для sport:
{"category": "sport", "activity": "тип активности", "duration_min": число или null, "distance_km": число или null, "calories": число или null, "note": "доп. инфо", "date": "YYYY-MM-DD"}

Для study:
{"category": "study", "subject": "предмет", "task": "описание задачи", "deadline": "YYYY-MM-DD или null", "status": "выполнено/в процессе/предстоит", "date": "YYYY-MM-DD"}

Для diet:
{"category": "diet", "meal": "тип приёма пищи (завтрак/обед/ужин/перекус)", "food": "что съел", "calories": число или null, "protein_g": число или null, "carbs_g": число или null, "fat_g": число или null, "date": "YYYY-MM-DD"}

Если не можешь определить категорию — {"category": "unknown"}

Сегодняшняя дата будет передана в сообщении. Используй её как date если другая не указана."""

def parse_message(text: str, today: str) -> dict:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Сегодня {today}. Сообщение: {text}"},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# ── Запись в листы ─────────────────────────────────────────────────────────────
def write_finance(data: dict):
    ws = get_sheet("Финансы")
    if ws.row_count < 2 or ws.cell(1, 1).value != "Дата":
        ws.update("A1:E1", [["Дата", "Тип", "Сумма (₽)", "Описание", "Добавлено"]])
    ws.append_row([
        data.get("date", ""),
        data.get("type", ""),
        data.get("amount", ""),
        data.get("description", ""),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

def write_sport(data: dict):
    ws = get_sheet("Спорт")
    if ws.row_count < 2 or ws.cell(1, 1).value != "Дата":
        ws.update("A1:G1", [["Дата", "Активность", "Время (мин)", "Дистанция (км)", "Калории", "Заметка", "Добавлено"]])
    ws.append_row([
        data.get("date", ""),
        data.get("activity", ""),
        data.get("duration_min", ""),
        data.get("distance_km", ""),
        data.get("calories", ""),
        data.get("note", ""),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

def write_study(data: dict):
    ws = get_sheet("Учёба")
    if ws.row_count < 2 or ws.cell(1, 1).value != "Дата":
        ws.update("A1:F1", [["Дата", "Предмет", "Задача", "Дедлайн", "Статус", "Добавлено"]])
    ws.append_row([
        data.get("date", ""),
        data.get("subject", ""),
        data.get("task", ""),
        data.get("deadline", ""),
        data.get("status", ""),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

def write_diet(data: dict):
    ws = get_sheet("Диета")
    if ws.row_count < 2 or ws.cell(1, 1).value != "Дата":
        ws.update("A1:H1", [["Дата", "Приём пищи", "Что съел", "Калории", "Белки (г)", "Углеводы (г)", "Жиры (г)", "Добавлено"]])
    ws.append_row([
        data.get("date", ""),
        data.get("meal", ""),
        data.get("food", ""),
        data.get("calories", ""),
        data.get("protein_g", ""),
        data.get("carbs_g", ""),
        data.get("fat_g", ""),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

WRITERS = {
    "finance": write_finance,
    "sport": write_sport,
    "study": write_study,
    "diet": write_diet,
}

CATEGORY_LABELS = {
    "finance": "💰 Финансы",
    "sport":   "🏃 Спорт",
    "study":   "📚 Учёба",
    "diet":    "🥗 Диета",
}

# ── Telegram хендлер ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    today = datetime.now().strftime("%Y-%m-%d")

    await update.message.reply_text("⏳ Обрабатываю...")

    try:
        data = parse_message(text, today)
        category = data.get("category")

        if category == "unknown" or category not in WRITERS:
            await update.message.reply_text(
                "🤔 Не смог определить категорию.\n\n"
                "Попробуй написать точнее, например:\n"
                "• «потратил 500р на кофе»\n"
                "• «пробежал 5км за 28 минут»\n"
                "• «сдал лабу по физике»\n"
                "• «съел гречку с курицей, 400 ккал»"
            )
            return

        WRITERS[category](data)
        label = CATEGORY_LABELS[category]

        # Формируем читаемый ответ
        lines = [f"✅ Записано в {label}"]
        for k, v in data.items():
            if k in ("category",) or v in (None, "", "null"):
                continue
            lines.append(f"  {k}: {v}")

        await update.message.reply_text("\n".join(lines))

    except json.JSONDecodeError:
        await update.message.reply_text("❌ Ошибка парсинга ответа AI. Попробуй ещё раз.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ── Запуск ─────────────────────────────────────────────────────────────────────
def main():
    token = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
