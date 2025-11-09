import logging
import time
from contextlib import contextmanager

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


class BrowserUtils:
    def __init__(self, driver: webdriver.Chrome) -> None:
        self.driver = driver

    def refresh(
        self,
    ):
        """Refresca la página actual y espera explícitamente 5 segundos."""
        self.driver.refresh()
        self.driver.implicitly_wait(5)

    def simulate_escape(self):
        logger.info("Simulando escape...")
        body_box = self.driver.find_element("tag name", "body")
        time.sleep(1)
        body_box.send_keys(Keys.ESCAPE)

    @contextmanager
    def select_modal(self, xpath: str):
        wait = WebDriverWait(self.driver, 5)
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()
        try:
            yield
        finally:
            self.simulate_escape()
            self.simulate_escape()
