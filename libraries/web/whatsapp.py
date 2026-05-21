from selenium.webdriver.common.by import By
from typing import Callable

from libraries.utils import logwarning, sublog
from libraries.web.browser import Browser


class BrowseWhatsApp(Browser):
    """
    Class for automating interactions with WhatsApp Web.
    Extends the Browser class with WhatsApp-specific functionality.
    """

    def __init__(
            self, 
            visible: bool = True, 
            browserDir: str | None = None, 
            userDataDir: str | None = None, 
            phonenumer: str | None = None, 
            user: str | None = None) -> None:
        """
        Initialize a WhatsApp Web automation instance.
        
        Args:
            visible: Whether the browser should be visible
            browserDir: Directory path to the browser executable
            userDataDir: Directory path to the user data directory
            phonenumer: User's phone number for authentication
            user: Alternative user identifier if phone number is not provided
        """
        super().__init__(visible, browserDir, userDataDir)
        self._user: str | None = phonenumer if phonenumer else user
        self._previousNumberNotFound = False

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def user(self) -> str | None:
        """Get the user identifier (phone number or username)."""
        return self._user
    
    @user.setter
    def user(self, value: str) -> None:
        """
        Set the user identifier.
        
        Args:
            value: User identifier to set
        """
        self._user = value

    @property
    def phonenumber(self) -> str | None:
        """Get the phone number (alias for user)."""
        return self._user
    
    @phonenumber.setter
    def phonenumber(self, value: str) -> None:
        """
        Set the phone number.
        
        Args:
            value: Phone number to set
        """
        self._user = value

    @property
    def isLoggedIn(self):
        """Check if user is logged in."""
        return self._is_logged_in()

    # =============================================================================
    # Navigation and Authentication Methods
    # =============================================================================
    
    def goTo(self) -> 'BrowseWhatsApp':
        """
        Navigate to WhatsApp Web and wait for page to load.
        
        Returns:
            Self for method chaining
        """
        return super().goTo('https://web.whatsapp.com/').wait(5)
    
    def login(self, send_email_function: Callable[[str], None] | None = None) -> 'BrowseWhatsApp':
        """
        Login to WhatsApp Web using phone number authentication.
        
        If a send_email_function is provided, it will be called with the verification
        code that needs to be entered. Otherwise, manual login is expected.
        
        Args:
            send_email_function: Optional function to send verification code via email
            
        Returns:
            Self for method chaining
        """
        if self._is_logged_in(): 
            return self

        self.findElement('div', 'Log in with phone number').click()
        self.element(By.XPATH, '//input[@type="text"]').input(self.user)
        self.findElement('button', 'Next').click()

        if send_email_function:
            code = self.element(By.XPATH, '//div[@aria-label="Enter code on phone:"]').text
            send_email_function(code)
        else:
            logwarning('No function provided to send email for code insertion')
            sublog('Waiting at intervals of 15 seconds to allow manual login')
      
        while not self._is_logged_in(): 
            self.wait(15)

        return self
    
    # =============================================================================
    # Messaging Methods
    # =============================================================================
    
    def sendMessage(
            self, 
            phonenumber: str | None = None, 
            content: list[str] | None = None, 
            recipient: str | None = None) -> bool:
        """
        Send a message to a contact in WhatsApp.
        
        Args:
            phonenumber: Recipient's phone number
            content: List of message paragraphs to send
            recipient: Alternative recipient identifier if phone number is not provided
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        # Start new chat only if previous number was not found, otherwise stay the search screen
        if not self._previousNumberNotFound:
            self.element(By.XPATH, '//button[@aria-label="New chat"]')
            self.click()
            self.wait(1)
     
        # Search for contact
            self.element(By.XPATH, '//input[@placeholder="Search name or number"]')
        self.clear()
     
        self.input(phonenumber)
        self.wait(1)

        # Select contact from search results
        clicked = False
        names = recipient.split(' ')
        for text in (phonenumber[-3:], names[-1], names[0]):
            try:
                self.element(By.XPATH, f'//div[@role="button"][contains(., "{text}")]')
                self.click()
                clicked = True
                break
            except Exception as e:
                pass
        if not clicked:
            self._previousNumberNotFound = True
            return False
        
        self._previousNumberNotFound = False
        
        # Type message
        try: 
            self.element(By.XPATH, '//div[@aria-placeholder="Type a message"]')
            self.clear()
        except:
            print('Element not found')
            return False

        content = content if isinstance(content, list) else content.split('\n')
        # Send message paragraphs
        for paragraph in content or []: 
            self.fasterInput(paragraph)
            self.enter(wait=1)
        return True

    # =============================================================================
    # Helper Methods
    # =============================================================================

    def _is_logged_in(self) -> bool:
        """
        Check if user is logged in to WhatsApp Web.
        
        Returns:
            True if logged in, False otherwise
        """
        return 'Log in with phone number' not in self.html