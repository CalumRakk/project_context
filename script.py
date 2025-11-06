from project_context.driver import DriverManager
from project_context.utils import load_chats, save_chats

project_path = r"D:\github Leo\servercontrol"

chats = load_chats()
if not chats:
    path_cookies = "aistudio.google.com_cookies.txt"
    driver = DriverManager(cookies_path=path_cookies)
    chats = driver.get_chats()
    save_chats(chats)

print("Cookies set successfully.")
