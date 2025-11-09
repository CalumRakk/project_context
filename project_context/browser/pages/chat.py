from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from project_context.browser.utils import BrowserUtils


class ChatBrowser:
    def __init__(self, driver):
        self.driver = driver
        self.utils = BrowserUtils(driver)

    @property
    def model_current(self):
        if not hasattr(self, "_model_current"):
            self._model_current = self._get_model_current()
        return self._model_current

    def _get_model_current(self):
        self.open()
        return self.driver.find_element(
            "xpath", "//span[@data-test-id='model-name']"
        ).text.strip()

    def open(self):
        if "prompts" in self.driver.current_url:
            return

        url = "https://aistudio.google.com/app/prompts/new_chat"
        self.driver.get(url)

        wait = WebDriverWait(self.driver, 5)
        wait.until(
            EC.presence_of_element_located(("xpath", "//button[@aria-label='Run']"))
        )

        # actualizar el nombre del modelo
        self._model_current = self._get_model_current()

    def _open_model_selection_and_click_all(self):
        wait = WebDriverWait(self.driver, 5)

        wait.until(
            EC.presence_of_element_located(
                (
                    "xpath",
                    "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
                )
            )
        )
        all_element = wait.until(
            EC.presence_of_element_located(
                (
                    "xpath",
                    ".//button[@variant='filter-chip' and contains(text(), 'All')]",
                )
            )
        )
        all_element.click()

    def get_models(self):
        """Devuelve los nombre de los modelos disponibles."""

        self.open()
        with self.utils.select_modal("//span[@data-test-id='model-name']") as _:
            self._open_model_selection_and_click_all()
            # Esperar a que se carguen los modelos
            wait = WebDriverWait(self.driver, 5)
            model_container = wait.until(
                EC.presence_of_element_located(
                    (
                        "xpath",
                        "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
                    )
                )
            )
            models = []
            for model in model_container.find_elements(
                "xpath", ".//ms-model-carousel-row"
            ):
                element_title = model.find_element(
                    "xpath", ".//span[@class='model-title-text ellipses']"
                )
                models.append(element_title.text)
            return models

    def select_model(self, model_name: str):
        models = self.get_models()
        if model_name not in models:
            raise Exception(f"Modelo {model_name} no encontrado")

        model_carousel_selector = self.driver.find_element(
            "xpath", "//ms-model-carousel[@data-test-id='model-carousel-in-selector']"
        )
        model_carousel_selector.find_element(
            "xpath", f".//ms-model-carousel-row//span[text()='{model_name}']"
        ).click()
