import time
import logging
import requests
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError
import telebot
import json
import threading
import queue
import os
import sqlite3
import re
import queue
import random
import socket
from telebot import apihelper

with open("botToken.txt", "r", encoding="utf-8") as f:
    TOKEN = f.readline()

users_data = {}

toMessengerMessages = queue.Queue()
subscribers_lock = threading.Lock()

bot = telebot.TeleBot(TOKEN)
notification_queue = queue.Queue()

if not os.path.exists("subscribers.json"):
    with open("subscribers.json", "a", encoding="utf-8") as file:
        file.write('{"subscribedChats":[]}')

START_MESSAGE = "Это бот помощник для LIn мессенджера. Через него вы можете привязать Telegram аккаунт к аккаунту LIn мессенджера, что бы иметь возможность востановить аккаунт LIn мессенджера в случае потери доступа к нему, а так же подписаться на уведомления о новых сообщениях в LIn мессенджере."



#Функции

def add_subscriber(chat_id):
    with subscribers_lock:
        with open("subscribers.json", "r", encoding="utf-8") as file:
            loadFile = json.loads(file.read())
        with open("subscribers.json", "w", encoding="utf-8") as file:
            loadFile['subscribedChats'].append(chat_id)
            file.write( json.dumps(loadFile, ensure_ascii=False))

def remove_subscriber(chat_id):
    with subscribers_lock:
        if os.path.exists("subscribers.json"):
            with open("subscribers.json", "r", encoding="utf-8") as file:
                loadFile = json.loads(file.read())
            with open("subscribers.json", "w", encoding="utf-8") as file:
                loadFile['subscribedChats'].remove(chat_id)
                file.write( json.dumps(loadFile, ensure_ascii=False))

def get_subscribers():
    with subscribers_lock:
        if os.path.exists("subscribers.json"):
            with open("subscribers.json", "r", encoding="utf-8") as file:
                loadFile = json.loads(file.read())
                return loadFile["subscribedChats"]

def checkIfHasTgLinked(chat_id):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id FROM Users WHERE tg_id = ?", (chat_id,))
        findedId = cursor.fetchone()
        return findedId if findedId != None else False

def linkTgId(chat_id, friendCode):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE Users SET tg_id = ? WHERE friend_code = ?", (chat_id, friendCode))
        conn.commit()

def unlinkTgId(friendCode):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE Users SET tg_id = NULL WHERE friend_code = ?", (friendCode,))
        conn.commit()

def getFriendCode(chat_id):
     with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT friend_code FROM Users WHERE tg_id = ?", (chat_id,))
        friendCode = cursor.fetchone()
        return friendCode[0] if friendCode else None

def checkIsFriendCodeExist(friendCode):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (friendCode,))
        userId = cursor.fetchone()
        return True if userId else False

def is_valid_friend_code(code: str) -> bool:
    pattern = r"^[A-Z1-9]{4}-[A-Z1-9]{4}-[A-Z1-9]{4}-[A-Z1-9]{4}$"
    return bool(re.match(pattern, code))

def sendMessageToMessenger(friend_code, text):
    toMessengerMessages.put((friend_code, text))

def main_menu_keyboard(chat_id):
    keyboard = telebot.types.InlineKeyboardMarkup()
    subscribe_text = "Включить уведомления" if chat_id not in get_subscribers() else "Выключить уведомления"
    subscribe_data = "subscribe" if chat_id not in get_subscribers() else "unsubscribe"
    link_text = "Привязать аккаунт" if not checkIfHasTgLinked(chat_id) else "Отвязать аккаунт"
    link_data = "link" if not checkIfHasTgLinked(chat_id) else "unlink"
    recoverButton = telebot.types.InlineKeyboardButton(text="Восстановить LInM аккаунт", callback_data="recoverRequest")
    keyboard.add(
        telebot.types.InlineKeyboardButton(text=link_text, callback_data=link_data)
    )
    if checkIfHasTgLinked(chat_id):
        keyboard.add(
            recoverButton, 
            telebot.types.InlineKeyboardButton(text=subscribe_text, callback_data=subscribe_data)
        )
    return keyboard

def checkIfUsersData(chat_id):
    if chat_id not in users_data: 
        users_data[chat_id] = ["", getFriendCode(chat_id), random.randint(100000, 999999)]

def send_to_server(friend_code, text):
    try:
        # Создаем временное или постоянное подключение к серверу
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 9999))
        data = json.dumps({"friend_code": friend_code, "text": text})
        s.sendall(data.encode('utf-8'))
        s.close()
    except Exception as e:
        print(f"Не удалось отправить данные серверу: {e}")

def server_ipc_listener():
    """Бот слушает команды от сервера (например, отправить код восстановления)"""
    ipc_bot_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ipc_bot_server.bind(('127.0.0.1', 9998)) 
    ipc_bot_server.listen(5)
    
    while True:
        conn, addr = ipc_bot_server.accept()
        try:
            data = conn.recv(1024).decode('utf-8')
            if data:
                cmd = json.loads(data)
                action = cmd.get("action")
                chat_id = cmd.get("chat_id")
                
                if action == "validate_recover":
                    # Вызываем функцию, которая раньше вызывалась напрямую
                    validateRecover(chat_id)
                elif action == "send_notification":
                    text = cmd.get("text")
                    send_notification(text, chat_id)
        except Exception as e:
            print(f"Ошибка IPC в боте: {e}")
        finally:
            conn.close()



# Обработчики команды start

@bot.message_handler(commands=['start'])
def start(message):
    global users_data
    chatId = message.chat.id
    checkIfUsersData(chatId)

    bot.send_message(
        chatId, 
        START_MESSAGE,
        reply_markup=main_menu_keyboard(chatId)
        )
    
    users_data[message.chat.id] = ["", getFriendCode(message.chat.id), random.randint(100000, 999999)]



#Обработка callback

@bot.callback_query_handler(func=lambda call: call.data == 'subscribe')
def subscribeCall(call):
    chatId = call.message.chat.id
    checkIfUsersData(chatId)
    add_subscriber(chatId)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=START_MESSAGE,
    )
    bot.send_message(chatId, "Вы успешно подписались на уведомления.")
    bot.send_message(
    chatId, 
    START_MESSAGE, 
    reply_markup=main_menu_keyboard(chatId)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'unsubscribe')
def unsubscribeCall(call):
    chatId = call.message.chat.id
    checkIfUsersData(chatId)
    remove_subscriber(chatId)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=START_MESSAGE,
    )
    bot.send_message(chatId, "Вы успешно отписались от уведомлений уведомления.")
    bot.send_message(
    chatId, 
    START_MESSAGE, 
    reply_markup=main_menu_keyboard(chatId)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'link')
def linkCall(call):
    checkIfUsersData(call.message.chat.id)
    keyboard = telebot.types.InlineKeyboardMarkup()
    resetButton = telebot.types.InlineKeyboardButton(text="Отмена", callback_data="reset")
    keyboard.add(resetButton)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=START_MESSAGE,
    )
    bot.send_message(call.message.chat.id, "Отправьте свой Friend code из LIn мессенджера. Найти его вы можете возле своеги имени, в поле подписанным Your friend-code.", reply_markup=keyboard)
    global users_data
    users_data.get(call.message.chat.id)[0] = "requestLink"
    
@bot.callback_query_handler(func=lambda call: call.data == 'unlink')
def unlinkCall(call):
    global users_data
    checkIfUsersData(call.message.chat.id)
    keyboard = telebot.types.InlineKeyboardMarkup()
    resetButton = telebot.types.InlineKeyboardButton(text="Отмена", callback_data="reset")
    keyboard.add(resetButton)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=START_MESSAGE,
    )
    bot.send_message(call.message.chat.id, f"Внимание! Если вы отвяжите Telegram аккаунт от своего аккаунт в LIn мессенджере, то не сможете востановить свой аккаунт в случае потери доступа в нему. Введит VALIDATE-UNLINK-{users_data.get(call.message.chat.id)[2]} что бы подтвердить отвязку.", reply_markup=keyboard)
    users_data.get(call.message.chat.id)[0] = "requestUnlink"

@bot.callback_query_handler(func=lambda call: call.data == 'recoverRequest')
def recoverRequestCall(call):
    keyboard = telebot.types.InlineKeyboardMarkup()
    backButton = telebot.types.InlineKeyboardButton(text="Назад", callback_data="back")
    keyboard.add(backButton)
    friendCode = getFriendCode(call.message.chat.id)
    global users_data
    checkIfUsersData(call.message.chat.id)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=START_MESSAGE,
    )
    bot.send_message(call.message.chat.id, f"Ваш Friend code: {friendCode}. Введите его в LIn мессенджере, в окне восстановления аккаунта. Открыть это окно можно по кнопке возле поля с вашим текущим Friend code.", reply_markup=keyboard)
    users_data.get(call.message.chat.id)[2] = random.randint(100000, 999999)

@bot.callback_query_handler(func=lambda call: call.data == 'reset')
def resetCall(call):
    global users_data
    checkIfUsersData(call.message.chat.id)
    users_data.get(call.message.chat.id)[0] = ""
    

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Действие отменено."
    )
    bot.send_message(
    call.message.chat.id, 
    START_MESSAGE,
    reply_markup=main_menu_keyboard(call.message.chat.id)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'back')
def backCall(call):
    global users_data
    users_data.get(call.message.chat.id)[0] = ""
    checkIfUsersData(call.message.chat.id)

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=call.message.text
    )
    bot.send_message(
    call.message.chat.id, 
    START_MESSAGE,
    reply_markup=main_menu_keyboard(call.message.chat.id)
    )

#Обработка текстовых сообщений

@bot.message_handler(content_types=['text'])
def getMessages(message):
    global users_data
    checkIfUsersData(message.chat.id)

    keyboard = telebot.types.InlineKeyboardMarkup()
    resetButton = telebot.types.InlineKeyboardButton(text="Отмена", callback_data="reset")
    keyboard.add(resetButton)
    
    if users_data.get(message.chat.id)[0] == "requestLink":
        if is_valid_friend_code(message.text) and checkIsFriendCodeExist(message.text):
            bot.send_message(message.chat.id, "Проверьте LIn мессенджер. Там вам должен был прийти код подтверждения от LIn_bot. Отправьте этот код сюда.", reply_markup=keyboard)
            
            users_data.get(message.chat.id)[1] = message.text
            users_data.get(message.chat.id)[2] = random.randint(100000, 999999)
            
            send_to_server(users_data.get(message.chat.id)[1], users_data.get(message.chat.id)[2])
            users_data.get(message.chat.id)[0] = "requestLinkValidate"
        
        elif is_valid_friend_code(message.text):
            bot.send_message(message.chat.id, "Пользователя с таким кодом не существует. Проверь корректность отправленного вами кода.", reply_markup=keyboard)
        
        else:
            bot.send_message(message.chat.id, "Отправленный вами код некорректен. Код должен быть в формате A1B2-C3D4-E5F6-G7H8. В сообщение не должно быть ничего кроме кода. Попробуйте отправить код ещё раз.", reply_markup=keyboard)
    
    elif users_data.get(message.chat.id)[0] == "requestLinkValidate":
        if message.text == str(users_data.get(message.chat.id)[2]):
            keyboard = telebot.types.InlineKeyboardMarkup()
            backButton = telebot.types.InlineKeyboardButton(text="Назад", callback_data="back")
            keyboard.add(backButton)
            bot.send_message(message.chat.id, "Ваш Telegram аккаунт успешно привязан.", reply_markup=keyboard)
            linkTgId(message.chat.id, users_data.get(message.chat.id)[1])
            users_data.get(message.chat.id)[0] = ""
        else:
            bot.send_message(message.chat.id, "Не правильный код подтверждения. Проверьте корректность отправленого вами кода и попробуйте ещё раз.", reply_markup=keyboard)

    elif users_data.get(message.chat.id)[0] == "requestUnlink":
        if message.text == f"VALIDATE-UNLINK-{users_data.get(message.chat.id)[2]}":
            keyboard = telebot.types.InlineKeyboardMarkup()
            backButton = telebot.types.InlineKeyboardButton(text="Назад", callback_data="back")
            keyboard.add(backButton)
            unlinkTgId(users_data.get(message.chat.id)[1])
            bot.send_message(message.chat.id, "Отвязка успешно выполнена.", reply_markup=keyboard)
            users_data.get(message.chat.id)[0] = ""
        else:
            bot.send_message(message.chat.id, "Некорректный код подтверждения. Процесс отвязки отменён.")
            bot.send_message(
            message.chat.id, 
            START_MESSAGE,
            reply_markup=main_menu_keyboard(message.chat.id)
            )

            users_data.get(message.chat.id)[0] = ""

    elif users_data.get(message.chat.id)[0] == "recoverRequest":
        if message.text == f"VALIDATE-RECOVER-{users_data.get(message.chat.id)[2]}":
            bot.send_message(message.chat.id, "Восстановление успешно подтверждено")
            send_to_server(users_data.get(message.chat.id)[1], "VALIDATE-RECOVER")

# Обработчик блокировки бота
@bot.my_chat_member_handler()
def on_my_chat_member(update):
    if update.new_chat_member.status == "kicked":
        remove_subscriber(update.chat.id)

# Воркер для отправки уведомлений (работает в фоне)
def notification_worker():
    while True:
        queueItem = notification_queue.get()
        text, reciver = queueItem
        if reciver in get_subscribers():
            try:
                bot.send_message(reciver, text)
            except Exception:
                remove_subscriber(reciver)  # пользователь заблокировал бота
        notification_queue.task_done()

def send_notification(msgText, reciverId):
    notification_queue.put((msgText, reciverId))

def validateRecover(chat_id):
    checkIfUsersData(chat_id)
    bot.send_message(chat_id, f"Попытка восстановить LInM аккаунт. Для подтверждения введите VALIDATE-RECOVER-{users_data.get(chat_id)[2]}. Если это не вы игнорируйте это сообщение")
    users_data.get(chat_id)[0] = "recoverRequest"

# Запуск бота 
def run_bot():
    print("Telegram бот запущен")
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    while True:
        try:
            logger.info("Запуск бота...")
            bot.infinity_polling(
                timeout=90,           # Увеличиваем время ожидания ответа
                long_polling_timeout=30,
                logger_level=logging.ERROR,   
                restart_on_change=False,    
                skip_pending=True          # игнорировать старые сообщения после перезапуска
            )
        except (ConnectTimeout, ReadTimeout, ConnectionError) as e:
            logger.error(f"Сетевая ошибка: {e}. Перезапуск через 20 секунд...")
            time.sleep(20)
        except Exception as e:
            logger.exception(f"Неожиданная ошибка: {e}")
            time.sleep(20)
        else:
            # Если polling завершился без ошибки (например, бот остановлен вручную), выходим
            break

# Инициализация
threading.Thread(target=notification_worker, daemon=True).start()

# Запускаем слушателя в начале инициализации бота
threading.Thread(target=server_ipc_listener, daemon=True).start()

if __name__ == "__main__":
    run_bot()