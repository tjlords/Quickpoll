import os
import telebot
import re
import time

# Get environment variables
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OWNER_ID = int(os.getenv('BOT_OWNER_ID'))

if not API_TOKEN or not OWNER_ID:
    raise ValueError("Please set TELEGRAM_BOT_TOKEN and BOT_OWNER_ID environment variables")

bot = telebot.TeleBot(API_TOKEN)

def extract_quiz(data: str):
    """
    Flexible parser:
    - Detects question lines (numbered like 1., 1), 1 -, or bare)
    - Detects options in many styles: a), (A), A., a , etc.
    - Detects correct option by presence of ✅ or ✔
    - Detects explanation lines starting with Ex: / Explain: / Explanation:
    - Returns list of quizzes: each is dict {question, options, correct_id, explanation}
    """
    if not data:
        return []

    # Normalize line endings and split
    txt = data.replace('\r\n', '\n').replace('\r', '\n')
    raw_lines = txt.split('\n')

    # Trim lines but preserve empty lines as separators
    lines = [ln.rstrip() for ln in raw_lines]

    quizzes = []
    i = 0
    n = len(lines)

    # helper: check if a line looks like an option
    def is_option_line(s):
        return re.match(r'^\s*[\(\[]?[A-Da-d][\)\]\.\-\s]+', s) is not None

    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Determine if this is a question start:
        # Condition A: line starts with number like '1.' or '24)' etc.
        # Condition B: next non-empty line looks like option (a) ...
        num_start = re.match(r'^\s*\d+[\.\)\-\s]+', lines[i])
        next_line_idx = i + 1
        while next_line_idx < n and lines[next_line_idx].strip() == "":
            next_line_idx += 1
        next_line = lines[next_line_idx].strip() if next_line_idx < n else ""

        if num_start or is_option_line(next_line):
            # Build question text: may span multiple lines until an option line appears
            if num_start:
                question_text = re.sub(r'^\s*\d+[\.\)\-\s]*', '', lines[i]).strip()
                i += 1
            else:
                question_text = lines[i].strip()
                i += 1

            # accumulate continuation lines until options start
            while i < n and not is_option_line(lines[i]) and lines[i].strip() != "" and not re.match(r'^\s*(Ex:|Explain:|Explanation:)', lines[i], re.I):
                # If next line is not an option or explanation, treat it as continuation of question
                question_text += " " + lines[i].strip()
                i += 1

            # collect options (expect at least 2, up to 10)
            options = []
            correct_idx = None
            while i < n and is_option_line(lines[i]) and len(options) < 10:
                opt_line = lines[i].strip()
                # extract option text after label
                m = re.match(r'^\s*[\(\[]?([A-Da-d])[\)\]\.\-\s]+(.*)$', opt_line)
                if m:
                    opt_text = m.group(2).strip()
                else:
                    # fallback: remove first token
                    opt_text = re.sub(r'^[^\w\d]*(\w+)[\)\.\-\s]*', '', opt_line).strip()

                # detect check mark
                if '✅' in opt_text or '✔' in opt_text:
                    opt_text = opt_text.replace('✅', '').replace('✔', '').strip()
                    if correct_idx is None:
                        correct_idx = len(options)
                options.append(opt_text)
                i += 1

            # After options, optionally an explanation line
            explanation = None
            if i < n and re.match(r'^\s*(Ex:|Explain:|Explanation:)', lines[i], re.I):
                # get the rest of that line after the marker
                explanation = re.sub(r'^\s*(Ex:|Explain:|Explanation:)\s*', '', lines[i], flags=re.I).strip()
                i += 1
                # also allow multi-line explanation (collect following non-empty lines until next question or blank)
                while i < n and lines[i].strip() != "" and not re.match(r'^\s*\d+[\.\)\-\s]+', lines[i]) and not is_option_line(lines[i]):
                    explanation += " " + lines[i].strip()
                    i += 1

            # sanity checks and defaults
            question_text = question_text.strip()
            if not question_text:
                # skip invalid block
                continue

            if len(options) < 2:
                # not enough options, skip attempt (possibly not a real question)
                continue

            if correct_idx is None:
                correct_idx = 0

            # truncate to safe Telegram limits
            if len(question_text) > 300:
                question_text = question_text[:300] + " [...]"
            options = options[:10]
            if explanation:
                if len(explanation) > 400:
                    explanation = explanation[:400] + " [...]"
                hint = explanation + ""
            else:
                hint = ""

            quizzes.append({
                "question": question_text,
                "options": options,
                "correct_id": int(correct_idx),
                "explanation": hint
            })

            # continue loop (i already at next unprocessed line)
            continue

        # if not a question start, move on
        i += 1

    return quizzes


def is_owner(message):
    return message.from_user.id == OWNER_ID


@bot.message_handler(commands=['start'])
def start(message):
    if not is_owner(message):
        bot.reply_to(message, "❌ You are not authorized.")
        return
    bot.reply_to(message,
        "📘 Send your quiz text or .txt file.\n\n"
        "👉 Format examples supported:\n"
        "1. Question...\n"
        "a) Option 1\nb) Option 2 ✅\nc) Option 3\n(d) Option 4\nEx: Explanation (optional)\n\n"
        "After sending, reply with group ID, @username or type /here to post here."
    )


owner_questions = {}

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if not is_owner(message):
        return

    # if quizzes already stored, treat as group id or username
    if message.from_user.id in owner_questions and owner_questions[message.from_user.id]['quizzes']:
        group_id = message.text.strip()

        # special: if user types /here, post to current chat
        if group_id.lower() == "/here":
            quizzes = owner_questions[message.from_user.id]['quizzes']
            bot.reply_to(message, f"📤 Posting {len(quizzes)} quizzes here...")
            for q in quizzes:
                try:
                    bot.send_poll(
                        chat_id=message.chat.id,
                        question=q['question'],
                        options=q['options'],
                        type='quiz',
                        correct_option_id=q['correct_id'],
                        is_anonymous=True,
                        explanation=q['explanation']
                    )
                    time.sleep(1)
                except Exception as e:
                    bot.reply_to(message, f"❌ {str(e)}")
            owner_questions[message.from_user.id]['quizzes'] = []
            return

        quizzes = owner_questions[message.from_user.id]['quizzes']
        target_chat = group_id if not re.fullmatch(r"-?\d+", group_id) else int(group_id)

        bot.reply_to(message, f"📤 Posting {len(quizzes)} quizzes to {group_id}...")
        for q in quizzes:
            try:
                bot.send_poll(
                    chat_id=target_chat,
                    question=q['question'],
                    options=q['options'],
                    type='quiz',
                    correct_option_id=q['correct_id'],
                    is_anonymous=True,
                    explanation=q['explanation']
                )
                time.sleep(1)
            except Exception as e:
                bot.reply_to(message, f"❌ {str(e)}")
        owner_questions[message.from_user.id]['quizzes'] = []
        return

    # else parse as quiz content
    quizzes = extract_quiz(message.text)
    if not quizzes:
        bot.reply_to(message, "❌ No valid quizzes found. Make sure you send question + 2-10 options; mark correct option with ✅ and optional Ex: line.")
        return

    owner_questions[message.from_user.id] = {'quizzes': quizzes}
    bot.reply_to(message, f"✅ {len(quizzes)} quizzes parsed.\nSend group ID or @username, or type /here to post here.")


@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not is_owner(message):
        return
    try:
        file_info = bot.get_file(message.document.file_id)
        data = bot.download_file(file_info.file_path).decode('utf-8')
    except Exception as e:
        bot.reply_to(message, f"❌ Error reading file: {e}")
        return

    quizzes = extract_quiz(data)
    if not quizzes:
        bot.reply_to(message, "❌ No valid quizzes found in file.")
        return

    owner_questions[message.from_user.id] = {'quizzes': quizzes}
    bot.reply_to(message, f"✅ {len(quizzes)} quizzes loaded.\nSend group ID or /here to post here.")


if __name__ == "__main__":
    print("🤖 Quiz Bot running on Render...")
    bot.infinity_polling()