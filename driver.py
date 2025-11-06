import logging
from parser import SeleniumCookie, load_netscape_cookies
from pathlib import Path
from typing import Union

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


class DriverManager:
    def __init__(self, cookies_path: Union[str, Path], headless=False):
        self.headless = headless
        self.driver = self._start()
        cookies = load_netscape_cookies(cookies_path)
        self.set_cookies(cookies)

    def _start(self) -> webdriver.Chrome:
        """Inicializa el driver si no existe todavía."""

        logger.info("Iniciando driver...")
        options = webdriver.ChromeOptions()

        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--disable-extensions")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-session-crashed-bubble")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--no-sandbox")
        options.add_argument("--lang=en-US")

        service = ChromeService(executable_path=ChromeDriverManager().install())
        self._driver = webdriver.Chrome(service=service, options=options)
        self._driver.set_window_size(800, 600)
        logger.info("Driver iniciado.")
        return self._driver

    def refresh(self):
        """Refresca la página actual y espera explícitamente 5 segundos."""
        self.driver.refresh()
        self.driver.implicitly_wait(5)

    def get(self, url):
        self.driver.get(url)

    def close(self):
        self.driver.quit()

    def set_cookies(self, cookies: list[SeleniumCookie]):
        logger.info("Agregando cookies...")
        cookies_dict = cookies[0].model_dump()
        domain_url = cookies_dict["domain"]
        current_url = self.driver.current_url
        if domain_url not in current_url:

            self.driver.get(f"https://{domain_url.strip('.')}/")
            self.driver.implicitly_wait(5)

        for cookie in cookies:
            cookies_dict = cookie.model_dump()
            if cookies_dict["expiry"] == 0:
                cookies_dict.pop("expiry")

            self.driver.add_cookie(cookies_dict)

        self.refresh()
        self.simulate_escape()

    def simulate_escape(self):
        logger.info("Simulando escape...")
        body_box = self.driver.find_element("tag name", "body")
        body_box.send_keys(Keys.ESCAPE)

    def _wait_chat_history_loaded(self):
        role_value = "progressbar"
        wait = WebDriverWait(self.driver, 10)
        wait.until(
            EC.invisibility_of_element_located(("xpath", f".//*[@role='{role_value}']"))
        )

    def get_chats(self):
        url = "https://aistudio.google.com/app/library"
        self.driver.get(url)

        self._wait_chat_history_loaded()

        wait = WebDriverWait(self.driver, 5)
        rowgroups = wait.until(
            EC.presence_of_element_located(("xpath", ".//tbody[@role='rowgroup']"))
        )
        chats = []
        for row in rowgroups.find_elements("xpath", ".//tr[@role='row']"):
            second_element_td = row.find_elements("xpath", ".//td")[1]
            a_element = second_element_td.find_element("xpath", ".//a")
            chat_title = a_element.text
            chat_link = a_element.get_attribute("href")
            chats.append({"title": chat_title, "link": chat_link})
        return chats
