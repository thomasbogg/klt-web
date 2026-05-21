import os
import random
import regex as re
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    SessionNotCreatedException,
    TimeoutException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from seleniumbase import Driver
from time import sleep
from typing import Self
import undetected_chromedriver as uc
from libraries.utils import sublog, Object, logerror


class Browser:
    """
    Browser class to manage a web browser instance.
    """

    class Tabs(Object):
        """
        Tabs class to manage browser tabs.
        """
        
        def __init__(self, driver: Driver) -> None:
            """
            Initialize a new Tabs instance.
            
            Args:
                driver: The browser driver instance.
            """
            super().__init__()
            self._driver: Driver = driver
            self._wait = WebDriverWait(self._driver, 10)

        @property
        def current(self) -> int:
            """
            Get the index of the current tab.

            Returns:
                The index of the current tab.
            """
            return self._tabs().index(self._current())
        
        @property
        def main(self) -> int:
            """
            Get the index of the main tab.
            
            Returns:
                The index of the main tab.
            """
            return 0

        @property
        def all(self) -> list[str]:
            """
            Get all tab names.
            
            Returns:
                A list of all tab names.
            """
            return list(self._tabs())
        
        def new(self) -> 'Browser.Tabs':
            """
            Create a new tab with the given name.
            Switches to the new tab after creation.
            
            Args:
                name: The name of the new tab.
                
            Returns:
                The current instance of the Tabs class.
            """
            currentLength = len(self._tabs())
            self._driver.execute_script("window.open('');")
            self._wait.until(EC.number_of_windows_to_be(currentLength + 1))
            self._switch(self._tabs()[-1])
            return self

        def switchTo(self, order: int = 1) -> 'Browser.Tabs':
            """
            Switch to the specified tab.
            
            Args:
                order: The order of the tab to switch to.
                
            Returns:
                The current instance of the Tabs class.
            """
            if order > 0:
                order -= 1
            self._switch(self._tabs()[order])
            return self

        def switchToNext(self) -> 'Browser.Tabs':
            """
            Switch to the next tab.
            
            Args:
                order: The number of tabs to move forward.

                
            Returns:
                The current instance of the Tabs class.
            """
            self._switch(self._tabs()[self._tabs().index(self._current()) + 1])
            return self

        def switchToPrevious(self) -> 'Browser.Tabs':
            """
            Switch to the next tab.
            
            Args:
                order: The number of tabs to move forward.

                
            Returns:
                The current instance of the Tabs class.
            """
            self._switch(self._tabs()[self._tabs().index(self._current()) - 1])
            return self

        def close(self) -> 'Browser.Tabs':
            """
            Close the specified tab.
            Switches to the last tab after closing.

            Returns:
                The current instance of the Tabs class.
            """
            indexCurrent = self._tabs().index(self._current())
            self._driver.close()
            #self._tabs().pop(indexCurrent)
            self._switch(self._tabs()[-1])
            return self
        
        def _tabs(self) -> list[str]:
            """
            Get a list of all open windows.
            
            Returns:
                A list of window handles.
            """
            return self._driver.window_handles
        
        def _current(self) -> str:
            """
            Get the current window handle.
            
            Returns:
                The current window handle.
            """
            return self._driver.current_window_handle
        
        def _switch(self, id: str) -> 'Browser.Tabs':
            """
            Switch to the specified window handle.
            
            Args:
                id: The window handle to switch to.
                
            Returns:
                The current instance of the Tabs class.
            """
            self._driver.switch_to.window(id)
            return self
    
        def __str__(self) -> str:
            """
            Get a string representation of the Tabs instance.
            
            Returns:
                A string representation of the Tabs instance.
            """
            return f'Tabs({self._tabs()})'

    def __init__(
            self, 
            visible: bool = False, 
            browserDir: str = None, 
            userDataDir: str = None
    ) -> None:
        """
        Initialize a new Browser instance.
        
        Args:
            visible: If True, the browser will be visible.
            browserDir: Path to the browser directory.
            userDataDir: Path to the user data directory.
        """
        self._element = None
        self._content = None
        self._attempts = 0
        self._currentHeight = 0
        self._browserDir = browserDir
        self._userDataDir = userDataDir
        self._visible = visible
        self._set_driver()
        self._tabs: Browser.Tabs = Browser.Tabs(self._driver)
        self._pageNumber = 1
        self._wait = WebDriverWait(self._driver, 10)
    
    # Navigation methods

    def get(self) -> Driver:
        """
        Get the current browser driver instance.
        
        Returns:
            The browser driver instance.
        """
        return self._element
    
    def goTo(self, address: str, wait: int = 2) -> Self:
        """
        Go to a specific address in the browser.
        
        Args:
            address: The URL to navigate to.
            wait: The time to wait after navigating.
            
        Returns:
            The current instance of the Browser class.
        """
        self._driver.get(address)
        self._currentHeight = 0
        self._wait.until(EC.presence_of_element_located((By.TAG_NAME, 'html')))
        self.wait(wait)
        return self

    def home(self) -> Self:
        """
        Go to the home page (the first tab).
        
        Returns:
            The current instance of the Browser class.
        """
        return self.goTo()
    
    def reload(self) -> Self:
        """
        Reload the current page.
        
        Returns:
            The current instance of the Browser class.
        """
        self._driver.refresh()
        return self.wait(3)

    def scroll(self, toEnd: bool = True, toStart: bool = False) -> Self:
        """
        Scroll the page to the end or to a specific height.
        
        Args:
            toEnd: If True, scroll to the end of the page.
            toStart: If True, scroll to the start of the page.
            
        Returns:
            The current instance of the Browser class.
        """
        totalHeight: int = int(self._driver.execute_script("return document.body.scrollHeight"))

        if toStart:
            for i in range(self._currentHeight + 1, 0, -2):
                self._driver.execute_script(f"window.scrollTo(0, {i});")
                self._currentHeight = 0
                return self
        
        if toEnd and totalHeight == self._currentHeight: 
            return self
        
        for i in range(self._currentHeight + 1, totalHeight, 2):
            self._driver.execute_script(f"window.scrollTo(0, {i});")

        self._currentHeight = totalHeight
        return self.scroll()

    def frame(self, frame: str = None, default: bool = False) -> Self:
        """
        Switch to a frame or default content.
        
        Args:
            frame: The name or ID of the frame to switch to.
            default: If True, switch to the default content.
            
        Returns:
            The current instance of the Browser class.
        """
        if default: 
            self._driver.switch_to.default_content()
        elif frame is not None: 
            self._driver.switch_to.frame(frame)
        return self
    
    def wait(self, custom: int | float = 2) -> Self:
        """
        Wait for a specified time or a random time.
        
        Args:
            custom: If provided, wait for a random time between 1 and 3 times the custom value.
            
        Returns:
            The current instance of the Browser class.
        """
        sleep(random.randrange(1, 3) * custom)
        return self
    
    def quit(self) -> Self:
        """
        Quit the browser and close all tabs.
        
        Returns:
            The current instance of the Browser class.
        """
        self._driver.quit()
        return self
    
    # Element selection methods
    
    def element(self, mode: By, value: str, ) -> Self:
        """
        Find an element by its mode and value.
        
        Args:
            mode: The mode to find the element (e.g., ID, XPATH).
            value: The value to find the element.
            
        Returns:
            The current instance of the Browser class.
        """
        self._wait.until(EC.presence_of_element_located((mode, value)))
        self._element = self._driver.find_element(mode, value)
        return self
    
    def hasElement(self, mode: By, value: str) -> bool:
        """
        Check if an element exists by its mode and value.
        
        Args:
            mode: The mode to find the element (e.g., ID, XPATH).
            value: The value to find the element.
            
        Returns:
            True if the element exists, False otherwise.
        """
        try:
            self._driver.find_element(mode, value)
            return True
        except NoSuchElementException:
            return False

    def elements(self, mode: By, value: str) -> Self:
        """
        Find multiple elements by their mode and value.
        
        Args:
            mode: The mode to find the elements (e.g., ID, XPATH).
            value: The value to find the elements.
            
        Returns:
            The current instance of the Browser class.
        """
        self._element = self._driver.find_elements(mode, value)
        self._elements = self._element
        return self

    def tryElement(self, mode: By, value: str) -> Self | None:
        """
        Try to find an element by its mode and value with multiple attempts.
        
        Args:
            mode: The mode to find the element (e.g., ID, XPATH).
            value: The value to find the element.
            
        Returns:
            The current instance of the Browser class or None if element not found.
        """
        self.resetAttempts()
        while not self.reachedLimitOfAttempts():
            try:
                return self.element(mode, value)
            except NoSuchElementException: 
                self.addAttempt()
                self.reload()
                self.wait(self.totalAttempts())
        url = self._driver.current_url
        sublog(f'FATAL : Tried 3 times to locate element {value} on page {url}.')
        sublog('------SKIPPING------')
        return None

    def findElement(self, value: str, match: str) -> Self:
        """
        Find an element by tag name and match its text with a regex pattern.
        
        Args:
            value: The tag name of the element to find.
            match: The regex pattern to match the text of the element.
            
        Returns:
            The current instance of the Browser class.
        """
        elements: list = self._driver.find_elements(By.TAG_NAME, value)
        for element in elements:
            elementText: str = element.text.strip()
            # Check for regex match
            if re.search(match, elementText):
                self._element = element
                return self
            # Check for partial match
            if match.lower() in elementText.lower():
                self._element = element
                return self
        
        self._element = None
        return self
    
    # Element interaction methods
    
    def click(self, wait: int = 4) -> Self | bool:
        """
        Click the current element.
        
        Args:
            wait: The time to wait after clicking.
            
        Returns:
            The current instance of the Browser class or False if not clicked.
        """
        try:
            self._wait.until(EC.element_to_be_clickable(self._element)).click()
        except (AttributeError, ElementClickInterceptedException, TimeoutException) as e:
            if isinstance(e, AttributeError):
                logerror(f"Attribute error while clicking element: {e}")
                return False
            
            if isinstance(e, ElementClickInterceptedException):
                self.script('arguments[0].click();')

            if isinstance(e, TimeoutException):
                logerror(f"Element not clickable after waiting: {e}")
                return False
        return self.wait(wait)

    def clear(self) -> Self:
        """
        Clear the content of the current element.
        
        Returns:
            The current instance of the Browser class.
        """
        self._element.send_keys(Keys.CONTROL, 'a')
        self._element.send_keys(Keys.BACK_SPACE)
        return self

    def input(self, content: str, returns: int = 0, time: float = 0.2) -> Self:
        """
        Input text into the current element.
        
        Args:
            content: The text to input.
            returns: The number of times to press Enter after inputting the text.
            time: The time to wait between each character input.
            
        Returns:
            The current instance of the Browser class.
        """
        content = str(content)
        if len(content) == 0: 
            return self

        self._element.send_keys(content[0])
        if len(content) == 1: 
            for _ in range(returns): 
                self._element.send_keys(Keys.RETURN)
            return self

        return self.wait(time).input(content[1:])
    
    def fasterInput(self, content: str, returns: int = 0) -> Self:
        """
        Input text into the current element faster.
        
        Args:
            content: The text to input.
            returns: The number of times to press Enter after inputting the text.
            
        Returns:
            The current instance of the Browser class.
        """
        content = str(content).split(' ')
        for word in content:
            self._element.send_keys(word)
            self._element.send_keys(Keys.SPACE)
            self.wait(0.5)
        for _ in range(returns): 
            self._element.send_keys(Keys.RETURN)
        return self

    def keys(self, keys: str) -> Self:
        """
        Send keys to the current element.
        
        Args:
            keys: The keys to send.
            
        Returns:
            The current instance of the Browser class.
        """
        self._element.send_keys(keys)
        return self

    def enter(self, wait: int = 3) -> Self:
        """
        Press the Enter key on the current element.
        
        Args:
            wait: The time to wait after pressing Enter.
            
        Returns:
            The current instance of the Browser class.
        """
        self._element.send_keys(Keys.RETURN)
        return self.wait(wait)

    def selectByValue(self, value: str) -> Self:
        """
        Select an option from a dropdown by its value.
        
        Args:
            value: The value of the option to select.
            
        Returns:
            The current instance of the Browser class.
        """
        Select(self._element).select_by_value(value)
        return self

    def selectByVisibleText(self, value: str) -> Self:
        """
        Select an option from a dropdown by its visible text.
        
        Args:
            value: The visible text of the option to select.
            
        Returns:
            The current instance of the Browser class.
        """
        Select(self._element).select_by_visible_text(value)
        return self

    def script(self, command: str) -> Self:
        """
        Execute a JavaScript command on the current element.
        
        Args:
            command: The JavaScript command to execute.
            
        Returns:
            The current instance of the Browser class.
        """
        self._driver.execute_script(command, self._element)
        return self
    
    # Element properties and attributes
    
    @property
    def driver(self) -> Driver:
        """
        Get the current browser driver instance.
        
        Returns:
            The browser driver instance.
        
        Raises:
            Exception: If browser driver is not initialized.
        """
        if self._driver is None:
            raise Exception('Browser driver not initialized.')
        return self._driver

    @property
    def html(self) -> str:
        """
        Get the HTML source of the current page.
        
        Returns:
            The HTML source of the current page.
        """
        return self._driver.page_source

    @property
    def text(self) -> str | list[str]:
        """
        Get the text of the current element or elements.
        
        Returns:
            The text of the element or a list of texts if multiple elements.
        """
        if isinstance(self._element, list): 
            return list(map(lambda x: x.text.strip(), self._element))
        return self._element.text.strip()

    @property
    def option(self) -> str:
        """
        Get the text of the first selected option.
        
        Returns:
            The text of the first selected option.
        """
        return Select(self._element).first_selected_option.text.strip()

    @property
    def isChecked(self) -> bool:
        """
        Check if the current element is checked.
        
        Returns:
            True if the element is checked, False otherwise.
        """
        return bool(self.attribute('checked'))

    @property
    def isSet(self) -> bool:
        """
        Check if the current element is set (not None).
        
        Returns:
            True if the element is set, False otherwise.
        """
        return self._element is not None
    
    @property
    def tabs(self) -> 'Browser.Tabs':
        """
        Get the Tabs instance.
        
        Returns:
            The Tabs instance.
        """
        return self._tabs
    
    @property
    def isDisplayed(self) -> bool:
        """
        Check if the current element is displayed.
        
        Returns:
            True if the element is displayed, False otherwise.
        """
        return self._element.is_displayed()
    
    def attribute(self, content: str) -> str | list[str]:
        """
        Get the attribute of the current element.
        
        Args:
            content: The attribute to get.
            
        Returns:
            The value of the attribute or list of values if multiple elements.
        """
        if not isinstance(self._element, list): 
            return self._element.get_attribute(content)
        return list(map(lambda x: x.get_attribute(content), self._element))
    
    # Pagination methods
    
    @property
    def nextPage(self) -> int:
        """
        Go to the next page and return the new page number.
        
        Returns:
            The current page number.
        """
        self._pageNumber += 1
        return self._pageNumber

    @property
    def previousPage(self) -> int:
        """
        Go to the previous page and return the new page number.
        
        Returns:
            The current page number.
        """
        self._pageNumber -= 1
        return self._pageNumber

    def resetPages(self) -> Self:
        """
        Reset the page number to 1.
        
        Returns:
            The current instance of the Browser class.
        """
        self._pageNumber = 1
        return self
    
    # Attempt handling methods
    
    def addAttempt(self) -> Self:
        """
        Add an attempt to the current number of attempts.
        
        Returns:
            The current instance of the Browser class.
        """
        self._attempts += 1
        return self

    def reachedLimitOfAttempts(self) -> bool:
        """
        Check if the limit of attempts has been reached.
        
        Returns:
            True if the limit of attempts has been reached, False otherwise.
        """
        return self._attempts > 2

    def resetAttempts(self) -> Self:
        """
        Reset the number of attempts to 0.
        
        Returns:
            The current instance of the Browser class.
        """
        self._attempts = 0
        return self
    
    def totalAttempts(self) -> int:
        """
        Get the total number of attempts made.
        
        Returns:
            The current number of attempts.
        """
        return self._attempts
    
    # Driver setup methods

    def reset_driver(self) -> Self:
        """
        Reset the browser driver by quitting the current driver and setting a new one.
        
        Returns:
            The current instance of the Browser class.
        """
        self.quit()
        self._set_driver()
        self._tabs = Browser.Tabs(self._driver)
        return self
    
    def reset_user(self) -> Self:
        """
        Reset the user data directory by quitting the current driver, deleting the user data directory, and setting a new driver.
        
        Returns:
            The current instance of the Browser class.
        """
        self.quit()
        if self._userDataDir and os.path.exists(self._userDataDir):
            os.system(f'rm -rf {self._userDataDir}')
        self._set_driver()
        self._tabs = Browser.Tabs(self._driver)
        return self
    
    def _set_driver(self) -> Driver:
        """
        Set the browser driver.
        
        Args:
            visible: If True, the browser will be visible.
            browserDir: Path to the browser directory.
            userDataDir: Path to the user data directory.
            
        Returns:
            The browser driver instance.
        """
        binaryLocation: str = os.path.join(self._browserDir, 'chrome')

        max_attempts = 3
        attempt = 0
        
        while attempt < max_attempts:
            try: 
                self._driver: Driver = self._get_driver(binaryLocation)
                self._driver.maximize_window()
                return  # Exit the loop if driver is created successfully
            except SessionNotCreatedException as e: 
                attempt += 1
                sublog(f'WARNING: Chrome session could not be created (attempt {attempt}/{max_attempts}).')
                sublog(f'Error: {str(e)}')
                
                # Kill all chrome processes
                os.system('killall chrome')
                os.system('killall chromedriver')
                
                # Add a delay before retrying
                sleep(5)
                
                if attempt == max_attempts:
                    sublog('FATAL: Maximum retry attempts reached. Could not create Chrome session.')
                    raise

        # This should not be reached due to the raise above
        raise Exception("Failed to create browser driver")
    
    def _get_driver(self, binaryLocation: str) -> Driver:
        """
        Get the browser driver instance.
        
        Args:
            visible: If True, the browser will be visible.
            userDataDir: Path to the user data directory.
            binaryLocation: Path to the browser binary.
            
        Returns:
            The browser driver instance.
        """
        # Create and use options properly
        options = self._set_options(binaryLocation)
        
        driver = Driver(
            uc=True,
            headless=not self._visible,
            user_data_dir=self._userDataDir,
            binary_location=binaryLocation,
            do_not_track=True,
            #chrome_options=options,  # Pass the options to the driver
            #reconnect_attempts=3,    # Add reconnection attempts
            #connect_timeout=30       # Increase connection timeout
        )
        return driver

    def _set_options(self, binaryLocation: str) -> uc.ChromeOptions:
        """
        Set the browser options.
        
        Args:
            visible: If True, the browser will be visible.
            binaryLocation: Path to the browser binary.
            userDataDir: Path to the user data directory.
            
        Returns:
            The browser options instance.
        """
        options: uc.ChromeOptions = uc.ChromeOptions()
        options.binary_location = binaryLocation
    
        if not self._visible: 
            options.headless = True
            options.add_argument('--headless')
    
        if self._userDataDir is not None:
            options.add_argument(f'--user-data-dir={self._userDataDir}')
    
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--start-maximized')
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--disable-notifications')
        options.add_argument('--disable-infobars')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--remote-debugging-port=9222')
        
        # Add these options to help with the renderer connection issue
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-background-timer-throttling')
        
        return options