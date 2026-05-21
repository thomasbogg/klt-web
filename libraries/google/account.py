from libraries.utils import Object


class GoogleAccount(Object):
    """
    Represents a Google account with associated credentials and details.
    
    This class manages Google account information including email address,
    phone number, and paths to credential files.
    """
    
    def __init__(self, **kwargs) -> None:
        """
        Initialize a new GoogleAccount instance.
        
        Args:
            **kwargs: Dictionary of attributes to set on initialization
        """
        super().__init__(load=kwargs)
        self._without_prefix = False

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def emailAddress(self) -> str:
        """Get the email address associated with this account."""
        return self._get('emailAddress')

    @emailAddress.setter
    def emailAddress(self, value: str) -> None:
        """Set the email address for this account."""
        self._set('emailAddress', value)

    @property
    def credentials(self) -> str:
        """Get the file path to the credentials directory."""
        return self._get('credentials')
    
    @credentials.setter
    def credentials(self, value: str) -> None:
        """Set the file path to the credentials directory."""
        self._set('credentials', value)
    
    @property
    def details(self) -> dict:
        """Get additional details about this account."""
        return self._get('details')

    @details.setter
    def details(self, value: dict) -> None:
        """Set additional details about this account."""
        self._set('details', value)

    @property
    def phoneNumber(self) -> str:
        """
        Get the phone number, optionally without country prefix.
        
        Returns phone number without prefix if _without_prefix flag is set.
        """
        if not self._without_prefix:
            return self._get('phoneNumber')
        if '+' not in self._get('phoneNumber'):
            return self._get('phoneNumber')
        return ''.join(self._get('phoneNumber').split()[1:])
    
    @phoneNumber.setter
    def phoneNumber(self, value: str) -> None:
        """Set the phone number for this account."""
        self._set('phoneNumber', value)

    @property
    def local(self) -> bool:
        """Check if this account is configured for local credentials."""
        return self._get('local')
    
    @local.setter
    def local(self, value: bool) -> None:
        """Set whether this account is configured for local credentials."""
        self._set('local', value)

    # =============================================================================
    # Public Methods
    # =============================================================================

    def noPrefix(self) -> 'GoogleAccount':
        """
        Set flag to return phone number without country prefix.
        
        Returns:
            GoogleAccount: Self for method chaining
        """
        self._without_prefix = True
        return self
    
    def withoutPrefix(self) -> 'GoogleAccount':
        """
        Alias for no_prefix() - set flag to return phone number without country prefix.
        
        Returns:
            GoogleAccount: Self for method chaining
        """
        self._without_prefix = True
        return self

    def reset(self) -> 'GoogleAccount':
        """
        Reset the phone number format to include country prefix.
        
        Returns:
            GoogleAccount: Self for method chaining
        """
        self._without_prefix = False
        return self
    
    # =============================================================================
    # Magic Methods
    # =============================================================================
    
    def __getattr__(self, name: str) -> str:
        """
        Handle attribute access for convenience aliases.
        
        Args:
            name: Attribute name to access
            
        Returns:
            str: Value of the requested attribute
        """
        if name == 'email':
            return self.emailAddress
        if name == 'phone':
            return self.phoneNumber
        return super().__getattr__(name)

    def __setattr__(self, name: str, value: str) -> None:
        """
        Handle attribute setting for convenience aliases.
        
        Args:
            name: Attribute name to set
            value: Value to assign to the attribute
        """
        if name == 'email':
            self.emailAddress = value
            return
        if name == 'phone':
            self.phoneNumber = value
            return
        return super().__setattr__(name, value)