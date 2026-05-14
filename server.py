import re
import socket
import subprocess
import sys
import threading
import json
import secrets
import os
import sqlite3
from time import sleep
import time
import requests

recovery_states = {}

connectedClients = {}
clientLock = threading.Lock()
loop = None

BOT_HANDSHAKE_MESSAGE = "Добро пожаловать в LIn Мессенджер. Настоятельно рекомендуем привязать Telegram аккаунт к вашему аккаунту в LIn Мессенджере. \nЭто даст вам возможность получать уведомления о новых сообщения в Telegram, возможность восстановить аккаунт при утрате доступа к нему, а так же возможность входа в аккаунт с другого устройства. \nПривязать аккаунт можно через бота в Telegram @linm_notif_bot."
BOT_FRIEND_CODE = "BOT-CODE-FIXED"
BOT_UUID = "9468da4f-5d0a-4b0f-8c30-8dda39e53336"


GITHUB_LATEST_VERSION = "https://github.com/NaCl1984/LIn-messanger/releases/latest/download/LInM.exe"
GITHUB_VERSION_RAW = "https://raw.githubusercontent.com/NaCl1984/LIn-messanger/refs/heads/main/version.json"

LATEST_VERSION_DATA = {"latest":"2.0.0", "critical":"2.0.0"}

def update_version_from_github():
    global LATEST_VERSION_DATA # Убедитесь, что эта переменная объявлена как словарь
    while True:
        try:
            response = requests.get(GITHUB_VERSION_RAW, timeout=5)
            if response.status_code == 200:
                # ПРЕОБРАЗУЕМ ТЕКСТ В СЛОВАРЬ
                data = response.json() 
                
                # Извлекаем значения из полученного JSON
                new_latest = data.get("latest")
                new_critical = data.get("critical")

                # Обновляем, если версия изменилась
                if new_latest and new_latest != LATEST_VERSION_DATA.get("latest"):
                    LATEST_VERSION_DATA["latest"] = new_latest
                    LATEST_VERSION_DATA["critical"] = new_critical
                    print(f"Данные версии обновлены: {LATEST_VERSION_DATA['latest']} (Critical: {new_critical})")

        except Exception as e:
            print(f"Ошибка проверки версии на GitHub: {e}")
        
        time.sleep(300)

# Запускаем фоновый поток проверки
threading.Thread(target=update_version_from_github, daemon=True).start()

def init_db():
    # Если файла 'messenger.db' нет, он создастся автоматически
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу пользователей (IF NOT EXISTS - проверка на существование)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                friend_code TEXT UNIQUE NOT NULL,
                nickname TEXT NOT NULL,
                current_status INTEGER DEFAULT 0,
                tg_id BIGINT,
                uuid TEXT DEFAULT ''
            )
        ''')
        
        # Таблица контактов (связей)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Contacts (
                owner_id INTEGER,
                contact_id INTEGER,
                FOREIGN KEY(owner_id) REFERENCES Users(id),
                FOREIGN KEY(contact_id) REFERENCES Users(id)
            )
        ''')

        #таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER,
                receiver_id INTEGER,
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(sender_id) REFERENCES Users(id),
                FOREIGN KEY(receiver_id) REFERENCES Users(id)
            )
        ''')

        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", ("BOT-CODE-FIXED",))
        botUser = cursor.fetchone()
        if botUser is None:
            cursor.execute("INSERT INTO Users (friend_code, nickname, current_status, uuid) VALUES (?, ?, ?, ?)", (BOT_FRIEND_CODE, "LIn_bot", 1, BOT_UUID))
        conn.commit()

def getFriendList(clientCode):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (clientCode,))
        myId = cursor.fetchone()

        query = '''
            SELECT Users.nickname, Users.current_status, Users.friend_code
            FROM Contacts
            JOIN Users ON Contacts.contact_id = Users.id
            WHERE Contacts.owner_id = ?
        '''
        cursor.execute(query, (myId[0],))
        friendList = cursor.fetchall()  

        return friendList

def generateUniqueCode():
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789" 
    while True:
        # Генерируем новый код
        new_code = '-'.join([''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)])

        # Проверяем, есть ли он уже в базе
        with sqlite3.connect('messenger.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (new_code,))
            if cursor.fetchone() is None:
                return new_code # Код уникален, выходим из цикла

def sendMessage(data, reciverCode, senderCode):
    with clientLock:
        reciverSock = connectedClients.get(reciverCode)
        try:
            reciverSock.sendall((data.decode("utf-8") + "\n").encode("utf-8"))
        except:
            connectedClients[reciverCode] = None  # Если отправка не удалась, считаем клиента отключенным
    
    try:
        with sqlite3.connect('messenger.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tg_id FROM Users WHERE friend_code = ?", (reciverCode,))
            reciverTgId = cursor.fetchone()
        
        msg_data = json.loads(data.decode("utf-8"))
        if "text" in msg_data and msg_data.get("type") == "msg" and connectedClients.get(reciverCode) is None:
            # Формируем текст уведомления
            with sqlite3.connect('messenger.db') as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT nickname FROM Users WHERE friend_code = ?", (senderCode,))
                senderNickname = cursor.fetchone()

            text = msg_data.get("text", "")
            if reciverTgId is not None:
                tell_bot_send_notif({"action":"send_notification", "text":f"✉️ {senderNickname[0]}: {text}", "chat_id":reciverTgId[0]})
    except:
        pass

def dumpToMessageHistory(msg, senderCode):
    print(f"Попытка записать сообщение от {senderCode}: {msg}")
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        msg_data = json.loads(msg)
        sender_code = senderCode
        receiver_code = msg_data.get("receiverCode")
        text = msg_data.get("text", "")
        
        # Получаем id отправителя и получателя по их friend_code
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (sender_code,))
        sender_id = cursor.fetchone()
        if sender_id is None:
            return  # Отправитель не найден, выходим из функции
        sender_id = sender_id[0]

        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (receiver_code,))
        receiver_id = cursor.fetchone()
        if receiver_id is None:
            return  # Получатель не найден, выходим из функции
        receiver_id = receiver_id[0]

        # Вставляем сообщение в базу данных
        cursor.execute("INSERT INTO Messages (sender_id, receiver_id, text) VALUES (?, ?, ?)", (sender_id, receiver_id, text))
        conn.commit()
        print(f"В историю записано сообщение от {senderCode}: {msg}")

def sendHistory(senderCode, reciverCode):
    historyToSend = []
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (senderCode,))
        sender_id = cursor.fetchone()
        if sender_id is None:
            return  # Отправитель не найден, выходим из функции
        sender_id = sender_id[0]

        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (reciverCode,))
        receiver_id = cursor.fetchone()
        if receiver_id is None:
            return  # Получатель не найден, выходим из функции
        receiver_id = receiver_id[0]

        # Получаем все сообщения, где пользователь является отправителем или получателем
        cursor.execute('''
            SELECT Users.nickname, text FROM Messages 
            JOIN Users ON Messages.sender_id = Users.id
            WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
            ORDER BY Messages.id ASC
        ''', (sender_id, receiver_id, receiver_id, sender_id))
        
        messages = cursor.fetchall()
        
        for sender_nickname, text in messages:
            historyToSend.append({
                "from": sender_nickname,
                "text": text
            })
    clientsSock = connectedClients.get(senderCode)
    if clientsSock is None:
        return  # Клиент отключен, не отправляем историю
    clientsSock.sendall((json.dumps({"type": "sendHistory", "messages": historyToSend}, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"Отправлена история сообщений между {senderCode} и {reciverCode}: {historyToSend}")

def updateStatus(status, userCode):
     with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE Users SET current_status = ? WHERE friend_code = ?", (status, userCode,))
        conn.commit()

def updateNickname(nickname, userCode):
        with sqlite3.connect('messenger.db') as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE Users SET nickname = ? WHERE friend_code = ?", (nickname, userCode,))
            conn.commit()

def sendContacts(clientSock, clientCode):
    if clientSock is None: return
    try:
        data = json.dumps({"type": "sendContacts", "contacts": getFriendList(clientCode)}, ensure_ascii=False)
        clientSock.sendall((data + "\n").encode("utf-8"))
        print(f"Отправлен список контактов для {clientCode} - {getFriendList(clientCode)}")
    except (ConnectionResetError, BrokenPipeError):
        print(f"Не удалось отправить контакты {clientCode}: соединение разорвано")

def updateAllContacts(clientCode):
    c_sock = connectedClients.get(clientCode)
    if c_sock and c_sock.fileno() != -1:
        sendContacts(c_sock, clientCode)
    
    # 2. Обновляем его друзей
    friends = getFriendList(clientCode)
    for friend in friends:
        f_code = friend[2]
        f_sock = connectedClients.get(f_code)
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА:
        # Отправляем только если это не тот самый клиент, который отключается,
        # и если у друга сокет все еще живой. and f_code != BOT_FRIEND_CODE
        if f_code != clientCode and f_sock and f_sock.fileno() != -1 :
            sendContacts(f_sock, f_code)

def addFriend(clientCode, friendCode):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (friendCode,))
        friendDbId = cursor.fetchone()
        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (clientCode,))
        clientDbId = cursor.fetchone()
        cursor.execute("SELECT owner_id FROM Contacts WHERE owner_id = ? AND contact_id = ?", (clientDbId[0], friendDbId[0]))
        existingContact = cursor.fetchone()
        if (friendDbId is not None and clientDbId is not None) and (friendDbId[0] != clientDbId[0]) and (existingContact is None):
            cursor.execute("INSERT INTO Contacts VALUES (?,?)", (clientDbId[0], friendDbId[0]))
            cursor.execute("INSERT INTO Contacts VALUES (?,?)", (friendDbId[0], clientDbId[0]))
            conn.commit()
            return True
        return False

def bot_ipc_handler():
    """Слушает сообщения от процесса-бота через локальный сокет"""
    ipc_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ipc_server.bind(('127.0.0.1', 9999)) # Локальный порт для бота
    ipc_server.listen(1)
    
    while True:
        conn, addr = ipc_server.accept()
        print("Бот-процесс подключился к серверу")
        try:
            while True:
                data = conn.recv(4096).decode('utf-8')
                if not data: break
                
                # Обработка сообщения от бота (аналог старого processBotMessages)
                msg = json.loads(data)
                friend_code = msg.get('friend_code')
                text = msg.get('text')
                
                print(friend_code, text)
                if text != "VALIDATE-RECOVER":
                    with sqlite3.connect('messenger.db') as conn:
                        cursor = conn.cursor()
                        # Создаём системного пользователя-бота (если ещё не создан)
                        cursor.execute("SELECT id FROM Users WHERE friend_code = ?", ("BOT-CODE-FIXED",))
                        bot_user = cursor.fetchone()
                        if bot_user is None:
                            cursor.execute(
                                "INSERT INTO Users (friend_code, nickname, current_status) VALUES (?, ?, ?)",
                                ("BOT-CODE-FIXED", "LIn_bot", 1)
                            )

                            conn.commit()
                        
                        # Формируем сообщение от имени бота
                        msg_data = {
                            "type": "msg",
                            "senderCode": "BOT-CODE-FIXED",
                            "receiverCode": friend_code,
                            "text": text,
                            "from": "LIn_bot"
                        }
                        # Сохраняем в историю (используем вашу функцию dumpToMessageHistory)
                        dumpToMessageHistory(json.dumps(msg_data, ensure_ascii=False), msg_data["senderCode"])
                        
                        # Если получатель онлайн — отправляем немедленно
                        receiver_sock = connectedClients.get(friend_code)
                        if receiver_sock is not None:
                            try:
                                receiver_sock.sendall((json.dumps(msg_data, ensure_ascii=False) + "\n").encode("utf-8"))
                            except Exception:
                                connectedClients[friend_code] = None  # помечаем как офлайн
                elif text == "VALIDATE-RECOVER":
                    recovery_states[friend_code].set()

        except Exception as e:
            print(f"Связь с ботом потеряна: {e}")
        finally:
            conn.close()

def tell_bot_to_recover(chat_id):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 9998))
        payload = json.dumps({"action": "validate_recover", "chat_id": chat_id})
        s.sendall(payload.encode('utf-8'))
        s.close()
    except Exception as e:
        print(f"Не удалось связаться с ботом для восстановления: {e}")

def tell_bot_send_notif(msg):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 9998))
        payload = json.dumps(msg)
        s.sendall(payload.encode('utf-8'))
        s.close()
    except Exception as e:
        print(f"Не удалось связаться с ботом для восстановления: {e}")

def is_valid_friend_code(code: str) -> bool:
    pattern = r"^[A-Z1-9]{4}-[A-Z1-9]{4}-[A-Z1-9]{4}-[A-Z1-9]{4}$"
    return bool(re.match(pattern, code))

def handle_client(clientsSock):
    clientCode = ''
    authState = False
    userUuid = ''
    buffer = ""
    newData = ""
    try:
        
        while True:
            chunk = clientsSock.recv(4096).decode("utf-8")
            if not chunk:
                break
            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    newData = json.loads(line)
                except json.JSONDecodeError:
                    print("Ошибка декодирования JSON в строке:", line)
                    break 
            dataType = newData.get("type")
            print(f"Получено сообщение: {newData}")
            
            if dataType == "disconnect":
                break
            
            elif dataType == "handShake":
                data = newData
                if data.get("friendCode") and data.get("friendCode") in connectedClients:
                    connectedClients[data.get("friendCode")] = clientsSock
                    clientCode = data.get("friendCode")
                    with sqlite3.connect('messenger.db') as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id, uuid FROM Users WHERE friend_code = ?", (clientCode,))
                        userDbId, userUuid = cursor.fetchone()
                        if userDbId is not None:
                            if userUuid is not None:
                                if data.get("uuid") == userUuid:
                                    cursor.execute("UPDATE Users SET current_status = ?, nickname = ? WHERE friend_code = ?", (data.get("status"), data.get("nickname") ,clientCode))
                                    clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":True }, ensure_ascii=False) + "\n").encode("utf-8"))  
                                    authState = True
                                    print(f"{clientCode} выполнил вход по {userUuid}")
                                elif userUuid == "":
                                    cursor.execute("UPDATE Users SET uuid = ? WHERE friend_code = ?", (data.get("uuid"), clientCode))
                                    userUuid = data.get("uuid")
                                    clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":True }, ensure_ascii=False) + "\n").encode("utf-8"))  
                                    authState = True
                                    print(f"{clientCode} привязал uuid")
                                else:
                                    clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":False }, ensure_ascii=False) + "\n").encode("utf-8"))  
                                    authState = False
                                    print(f"{clientCode} пытался войти по uuid")
                                    with clientLock:
                                        if clientCode in connectedClients and connectedClients[clientCode] == clientsSock:
                                            connectedClients[clientCode] = None
                                    clientCode = ''
                                    continue
                        else:
                            clientCode = generateUniqueCode()
                            cursor.execute("INSERT INTO Users (friend_code, nickname, current_status, uuid) VALUES (?, ?, ?, ?)", (clientCode, data.get("nickname"), data.get("status"), data.get("uuid")))
                            dumpToMessageHistory(json.dumps({"text": BOT_HANDSHAKE_MESSAGE, "receiverCode":clientCode}), BOT_FRIEND_CODE)
                            clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":True }, ensure_ascii=False) + "\n").encode("utf-8"))  
                            authState = True
                            print(f"{clientCode} привязал uuid")
                        conn.commit()
                        
                else:
                    newFriendCode = generateUniqueCode()
                    print(f"Сгенерирован новый код: {newFriendCode} для клиента {clientsSock.getpeername()}")
                    clientCode = newFriendCode 
                    with sqlite3.connect('messenger.db') as conn:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO Users (friend_code, nickname, current_status, uuid) VALUES (?, ?, ?, ?)", (newFriendCode, data.get("nickname"), data.get("status"), data.get("uuid")))
                        conn.commit()
                        addFriend(newFriendCode, BOT_FRIEND_CODE)
                        dumpToMessageHistory(json.dumps({"text": BOT_HANDSHAKE_MESSAGE, "receiverCode":clientCode}), BOT_FRIEND_CODE)
                        connectedClients[newFriendCode] = clientsSock   
                        clientsSock.sendall((json.dumps({"type":"sendFriendCode", "friendCode":newFriendCode }, ensure_ascii=False) + "\n").encode("utf-8"))     
                        clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":True }, ensure_ascii=False) + "\n").encode("utf-8"))  
                        userUuid = data.get("uuid")
                        authState = True

                user_version = data.get("version")
                
                if LATEST_VERSION_DATA.get("latest") != user_version:
                    if LATEST_VERSION_DATA.get("critical") != user_version:
                        clientsSock.sendall((json.dumps({"type":"updateRequired", "critical":True , "link":GITHUB_LATEST_VERSION}, ensure_ascii=False) + "\n").encode("utf-8"))
                        print(f"Отправлено critical обновление {clientCode}")
                    else:
                        clientsSock.sendall((json.dumps({"type":"updateRequired", "critical":False , "link":GITHUB_LATEST_VERSION}, ensure_ascii=False) + "\n").encode("utf-8"))
                        print(f"Отправлено обновление {clientCode}")


                if authState:
                    # sendContacts(clientsSock, clientCode)
                    # sleep(0.2)
                    updateAllContacts(clientCode)

                    


                    continue


            elif dataType == "requestHistory" and authState:
                sendHistory(clientCode, newData.get("friendCode"))
            
            elif dataType == "updateStatus" and authState:
                updateStatus(newData.get("status"), clientCode)
                updateAllContacts(clientCode)
                print(f"Пользователь {clientCode} обновил статус на {newData.get('status')}")
            
            elif dataType == "updateNickname" and authState:
                updateNickname(newData.get("nickname"), clientCode)
                updateAllContacts(clientCode)
                print(f"Пользователь {clientCode} обновил ник на {newData.get('nickname')}")
            
            elif dataType == "addFriend" and authState:
                if is_valid_friend_code(newData.get("friendCode")):
                    if addFriend(clientCode, newData.get("friendCode")):
                        updateAllContacts(clientCode)
                        print(f"Пользователь {clientCode} добавил в друзья {newData.get('friendCode')}")
                    else:
                        print(f"Пользователь {clientCode} попытался добавить несуществующего пользователя {newData.get('friendCode')}")
                else:
                    print(f"Пользователь {clientCode} попытался добавить несуществующий код {newData.get('friendCode')}")
                
            
            elif dataType == "requestRecover":
                global recovery_states
                with sqlite3.connect('messenger.db') as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM Users WHERE friend_code = ?", (newData.get("friendCode"),))
                    friendDbId = cursor.fetchone()
                    if friendDbId is not None:
                        recovery_states[newData.get("friendCode")] = threading.Event()  
                        
                        cursor.execute("SELECT tg_id FROM Users WHERE id = ?", (friendDbId[0],))
                        tgId = cursor.fetchone()
                        
                        tell_bot_to_recover(tgId[0])

                        recovery_states[newData.get("friendCode")].wait(timeout=120)
                        
                        if recovery_states[newData.get("friendCode")].is_set():
                            clientsSock.sendall((json.dumps({"type":"recoverAnswer", "answer": True}, ensure_ascii=False) + "\n").encode("utf-8"))
                            clientsSock.sendall((json.dumps({"type":"authAnswer", "answer":True }, ensure_ascii=False) + "\n").encode("utf-8"))  
                            authState = True
                            with sqlite3.connect('messenger.db') as conn:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE Users SET uuid = ? WHERE friend_code = ?", (userUuid, clientCode))
                                conn.commit()
                            print(f"Отправлен ответ восстановления {clientCode}: True")
                        else:
                            clientsSock.sendall((json.dumps({"type":"recoverAnswer", "answer": False}, ensure_ascii=False) + "\n").encode("utf-8") )
                            print(f"Отправлен ответ восстановления {clientCode}: False")

                        recovery_states.pop(newData.get("friendCode"), None)  # Удаляем состояние восстановления после использования
                    else:
                        clientsSock.sendall((json.dumps({"type":"recoverAnswer", "answer": False}, ensure_ascii=False) + "\n").encode("utf-8"))
                        print(f"Отправлен ответ восстановления {clientCode}: False")
            
            elif dataType == "msg" and authState:
                with sqlite3.connect('messenger.db') as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT nickname FROM Users WHERE friend_code = ?", (clientCode,))
                    sender_db_res = cursor.fetchone()
                    sender_nickname = sender_db_res[0] if sender_db_res else "Unknown"

                # Добавляем данные в словарь сообщения
                newData["senderCode"] = clientCode
                newData["from"] = sender_nickname
                
                print(f"from: {clientCode} to: {newData.get('receiverCode')} text: {newData.get('text')}")
                dumpToMessageHistory(json.dumps(newData, ensure_ascii=False), clientCode)
                sendMessage(json.dumps(newData, ensure_ascii=False).encode("utf-8"), newData.get('receiverCode'), clientCode)
    except ConnectionResetError:
        pass
    finally:
        with clientLock:
            if clientsSock in connectedClients and clientCode:
                connectedClients[clientCode] = None
        if clientCode and authState:
            updateStatus(-1, clientCode)
            updateAllContacts(clientCode)
            
        print(f"Клиент {clientCode} отключился")
        clientsSock.close()
        

init_db()
print("База данных инициализирована")

# threading.Thread(target=processBotMessages, daemon=True).start()
# print("Соединение между сервером и ботом установлено")

with sqlite3.connect('messenger.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT friend_code FROM Users")
    users = cursor.fetchall()
    for user in users:
        connectedClients[user[0]] = None  # Инициализируем словарь подключенных клиентов
    # cursor.execute("UPDATE Users SET nickname = ?, current_status = ?, uuid = ? WHERE friend_code = ?", ("LIn bot", 1, BOT_UUID ,BOT_FRIEND_CODE))
    # conn.commit

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('', 8888))
server.listen(5)
print("Сервер запущен...")

vpn_python = os.path.join(os.path.dirname(sys.executable), "python_vpn.exe")
# print("Бот запущен через python_vpn.exe")
# Запуск IPC сервера и самого бота
threading.Thread(target=bot_ipc_handler, daemon=True).start()
# subprocess.Popen([vpn_python, "supportBot.py"])
print(vpn_python)


while True:
    clientSock, addr = server.accept()
    print(f"Подключился {addr}")
    thread = threading.Thread(target=handle_client, args=(clientSock,))
    thread.start()