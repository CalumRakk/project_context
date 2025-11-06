from project_context.driver import DriverManager

path_cookies = "aistudio.google.com_cookies.txt"
driver = DriverManager(cookies_path=path_cookies)

chats = driver.get_chats()


print("Cookies set successfully.")
