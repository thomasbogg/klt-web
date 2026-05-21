from googleapiclient.errors import HttpError
from time import sleep as _sleep
from typing import Any, Self

from libraries.google.connect import GoogleAPIService
from libraries.utils import (
    Object,
    check_hex,
    logwarning,
)


class GoogleForm(Object):
    """
    Represents a Google Form with methods for accessing and modifying form 
    properties.
    
    This class provides comprehensive functionality for creating, updating, and
    managing Google Forms through the Google Forms API.
    
    Args:
        connection: Google API service connection
        load: Dictionary containing form data to load
        name: Name of the form
        id: ID of the form
        body: Form body content
        TEST: Flag for test mode
    """

    class Option(Object):
        """
        Represents an option for a choice question in a Google Form.

        Kwargs:
            value: The text value of the option
            goToAction: Action to take when this option is selected
            goToSectionId: ID of the section to navigate to
            isCorrect: Whether this option is marked as correct (for quizzes)
        """
        def __init__(self, **kwargs) -> None:
            """
            Initialize a new option for a choice question.
            
            Kwargs:
                value: Text value of the option
                goToAction: Action to take when this option is selected
                goToSectionId: ID of the section to navigate to
                isCorrect: Whether this option is correct (for quizzes)
            """
            super().__init__(load=kwargs)
            
        @property
        def value(self) -> str:
            """
            Get the text value of the option.
            
            Returns:
                str: The text value of the option
            """
            return self._get('value')
        
        @value.setter
        def value(self, value: str | None) -> None:
            """
            Set the text value of the option.
            
            Args:
                value: New text value for the option
            """
            if not value:
                raise ValueError('Option value cannot be empty')
            if not isinstance(value, str):
                raise TypeError('Option value must be a string')
            self._set('value', value)

        @property
        def goToAction(self) -> str | None:
            """
            Get the action to take when this option is selected.
            
            Returns:
                str | None: The action type (e.g., 'CONTINUE', 'GO_TO_SECTION')
            """
            return self._get('goToAction')

        @goToAction.setter
        def goToAction(self, value: str) -> None:
            """
            Set the action to take when this option is selected.

            Valid values are 'NEXT_SECTION', 'RESTART_FORM', 'SUBMIT_FORM', or None.
            
            Args:
                value: The action type (e.g., 'NEXT_SECTION', 'SUBMIT_FORM', None)
            """
            if value not in [
                'GO_TO_ACTION_UNSPECIFIED', 
                'NEXT_SECTION',
                'RESTART_FORM', 
                'SUBMIT_FORM']:
                raise ValueError('Invalid goToAction value')
            self._set('goToAction', value)

        @property
        def goToSectionId(self) -> str | None:
            """
            Get the ID of the section to navigate to when this option is selected.

            Returns:
                str | None: The section ID or None if not applicable
            """
            return self._get('goToSectionId')

        @goToSectionId.setter
        def goToSectionId(self, value: str | None) -> None:
            """
            Set the ID of the section to navigate to when this option is selected.
            The ID must be in HEX format (e.g., '20000000').

            Args:
                value: The section ID or None if not applicable
            """
            if not check_hex(value):
                raise TypeError('goToSectionId must be a valid HEX string')
            return self._set('goToSectionId', value)
        
        @property
        def hasActions(self) -> bool:
            """
            Check if the option has any actions defined.
            
            Returns:
                bool: True if actions are defined, False otherwise
            """
            for action in ('goToAction', 'goToSectionId'):
                if action in self._values:
                    if self._values[action] is not None:
                        return True
            return False
        

    class Responses(Object):
        """
        Represents the responses to a Google Form.
        
        This class provides methods to access and manipulate form responses.
        
        Args:
            load: Dictionary containing response data to load
        """
        def __init__(self, load: dict | None = None) -> None:
            """
            Initialize a new Responses object.
            
            Args:
                load: Dictionary containing response data to load
            """
            super().__init__(load=load)
            # Check if GoogleForm.Responses has not been recursively called from 
            # GoogleForm.Responses.latest/earliest
            ## If it is recursive, self._values will be a dict of 'responses' list() already
            if 'responses' in self._values:
                ## Set self._values to be list() of all available responses
                ## in chronological order of submission
                self._values = self._set(self._values['responses'], list())

        @property
        def exist(self) -> bool:
            """
            Check if the form responses exist.
            """
            return bool(self._values)
        
        @property
        def hasMany(self) -> bool:
            """
            Check if there are multiple responses to the form.
            
            Returns:
                True if there are multiple responses, False otherwise
            """
            if not isinstance(self._values, list):
                return logwarning(
                    f'Checking total responses of {self.__class__.__name__} object but '
                    f'self._values (main) attr is not list() type but {type(self._values)} ' 
                    'type so returning *None*')
            return len(self._values) > 1
        
        @property
        def latest(self) -> 'GoogleForm.Responses':
            """
            Get the latest response to the form.
            
            Returns:
                GoogleForm.Responses: The latest response object
            """
            return self.__class__(self._values[-1])
        
        @property
        def earliest(self) -> 'GoogleForm.Responses':
            """
            Get the earliest response to the form.

            Returns:
                GoogleForm.Responses: The earliest response object
            """
            return self.__class__(self._values[0])
        
        @property
        def length(self) -> int:
            """
            Get the number of responses to the form.
            
            Returns:
                int: The number of responses
            """
            if not isinstance(self._values, list):
                return logwarning(
                    f'Checking total responses of {self.__class__.__name__} object but '
                    f'self._values (main) attr is not list() type but {type(self._values)} ' 
                    'type so returning *None*')
            return len(self._values)
        
        def answer(self, key: str) -> str | None:
            """
            Get the value of a specific answer from the form responses.

            Args:
                key: The key of the answer to retrieve
            
            Returns:
                The value of the answer or None if not found
            """
            return self._get(key)
        
        def _set(self, values: list, ordered: list) -> list | None:
            """
            Set the values in the form responses in chronological order.
            This method ensures that the responses are ordered by their
            creation time.

            Args:
                values: Dictionary containing response data
                ordered: List to store ordered responses

            Returns:
                List of ordered responses or None if not found
            """
            
            if not values:
                return ordered
            
            earliest: dict = values[0]
            for response in values[1:]:
                if response['createTime'] < earliest['createTime']:
                    earliest = response
            
            ordered.append(earliest)
            values.remove(earliest)
            return self._set(values, ordered)
        
        def _get(self, key: str) -> str | None:
            """
            Get the value from the form responses based on the key.

            Args:
                key: The key of the answer to retrieve

            Returns:
                The value of the answer or None if not found
            """
            if self._values is None:
                return logwarning(
                    f'Trying to retrieve answers from an empty form.')
            try:
                answerLocation = self._values['answers'][key]
                return answerLocation['textAnswers']['answers'][0]['value']
            except KeyError:
                return None


    def __init__(
            self, 
            connection: GoogleAPIService | None = None, 
            load: dict | None = None, 
            name: str | None = None, 
            id: str | None = None, 
            body: dict | None = None, 
            TEST: bool = False) -> None:
        """
        Initialize a Google Form object.
        
        Args:
            connection: Connection to Google API service
            load: Dictionary containing form data to load
            name: Name of the form
            id: ID of the form
            body: Form body content
            TEST: Flag for test mode
        """
        super().__init__(load, id, name, TEST)
        self._connection: GoogleAPIService | None = connection
        self._service = connection.connection if connection else None
        self._body: dict | None = body
        self._newItems: list[dict] = list()

    # =============================================================================
    # Wrapper Methods
    # =============================================================================

    def appendNewItem(func) -> Self:
        """
        Decorator to append a new item to the form.
        
        Args:
            func: Function to wrap

        Returns:
            Wrapped function that appends a new item
        """
        def wrapper(self, *args, **kwargs):
            self._newItems.append(func(self, *args, **kwargs))
            return self
        return wrapper

    # =============================================================================
    # Helper Methods
    # =============================================================================

    def _set(self, *keys: str, value: str | None = None) -> None:
        """
        Set a nested value in the form data structure.
        
        Args:
            *keys: Nested keys to traverse
            value: Value to set at the final key
        """
        if not value:
            return logwarning('No value provided for _set() in GoogleForm class')
       
        values = self._values
        for key in keys[:-1]:
            if key not in values:
                values[key] = {}
            values = values[key]
       
        values[keys[-1]] = value

    def _get(self, *keys: str) -> Any:
        """
        Get a nested value from the form data structure.
        
        Args:
            *keys: Nested keys to traverse
            
        Returns:
            Value at the specified key path
        """
        values = self._values
        for key in keys[:-1]:
            values = values[key]
        return values[keys[-1]]

    # =============================================================================
    # Connection Properties
    # =============================================================================

    @property
    def connection(self) -> GoogleAPIService | None:
        """
        Get the Google API service connection.
        
        Returns:
            GoogleAPIService object or None if not connected
        """
        return self._connection
    
    @connection.setter
    def connection(self, value: GoogleAPIService) -> None:
        """
        Set the Google API service connection.
        
        Args:
            value: GoogleAPIService connection object
        """
        self._connection = value
        self._service = value.connection

    @property
    def hasConnection(self) -> bool:
        """
        Check if the form has a valid API connection.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connection is not None

    # =============================================================================
    # Form Properties
    # =============================================================================

    @property
    def id(self) -> str | None:
        """
        Get the form ID.
        
        Returns:
            The ID of the form
        """
        if 'id' in self._values:
            return self._values['id']
        if 'formId' in self._values:
            return self._values['formId']
        return None
    
    @property
    def title(self) -> str | None:
        """
        Get the form title.
        
        Returns:
            The title of the form
        """
        return self._get('info', 'title')
    
    @title.setter
    def title(self, value: str) -> None:
        """
        Set the form title.
        
        Args:
            value: New form title
        """
        self._set('info', 'title', value=value)

    @property
    def documentTitle(self) -> str | None:
        """
        Get the document title.
        
        Returns:
            The title of the document
        """
        return self._get('info', 'documentTitle')
    
    @documentTitle.setter
    def documentTitle(self, value: str) -> None:
        """
        Set the document title.
        
        Args:
            value: New document title
        """
        self._set('info', 'documentTitle', value=value)

    @property
    def description(self) -> str | None:
        """
        Get the form description.
        
        Returns:
            The description of the form
        """
        return self._get('info', 'description')
    
    @description.setter
    def description(self, value: str) -> None:
        """
        Set the form description.
        
        Args:
            value: New form description
        """
        self._set('info', 'description', value=value)

    @property
    def isQuiz(self) -> bool | None:
        """
        Get the form quiz setting.
        
        Returns:
            The quiz setting of the form
        """
        return self._get('settings', 'quizSettings', 'isQuiz')
    
    @isQuiz.setter
    def isQuiz(self, value: bool) -> None:
        """
        Set the form quiz setting.
        
        Args:
            value: The quiz setting for the form
        """
        self._set('settings', 'quizSettings', 'isQuiz', value=value)

    @property
    def emailCollectionType(self) -> str | None:
        """
        Get the email collection type.

        Options:
            - DO_NOT_COLLECT
            - VERIFIED
            - RESPONDER_INPUT
        
        Returns:
            The email collection type of the form
        """
        return self._get('settings', 'emailCollectionType')
    
    @emailCollectionType.setter
    def emailCollectionType(self, value: str) -> None:
        """
        Set the form email collection type.

        Options:
            - DO_NOT_COLLECT
            - VERIFIED
            - RESPONDER_INPUT
        
        Args:
            value: The email collection type for the form
        """
        if value not in ['DO_NOT_COLLECT', 'VERIFIED', 'RESPONDER_INPUT']:
            raise ValueError("Invalid email collection type")
        self._set('settings', 'emailCollectionType', value=value)

    @property
    def responderUri(self) -> str | None:
        """
        Get the form responder URI.
        
        Returns:
            URI for form respondents
        """
        return self._get('responderUri')
    
    @property
    def responses(self) -> dict:
        """
        Get all responses for this form.
        
        Returns:
            Form response data from the API
        """
        return GoogleForm.Responses(
            self._service.forms().responses().list(formId=self.id).execute())
    
    @property
    def rawResponses(self) -> dict:
        """
        Get all responses for this form.
        
        Returns:
            Form response data from the API
        """
        return self._service.forms().responses().list(formId=self.id).execute()
    
    @property
    def content(self) -> dict:
        """
        Get the complete form content.
        
        Returns:
            Dictionary containing form content from the API
        """
        return self._service.forms().get(formId=self.id).execute()
    
    @property
    def isPublished(self) -> bool | None:
        """
        Get the form published status.
        
        Returns:
            True if the form is published, False otherwise
        """
        try:
            return self._get('publishSettings', 'publishState', 'isPublished')
        except KeyError:
            return None
        
    @isPublished.setter
    def isPublished(self, value: bool) -> None:
        """
        Set the form published status.
        
        Args:
            value: Published status for the form
        """
        self._set('publishSettings', 'publishState', 'isPublished', value)

    # =============================================================================
    # API Interaction Methods
    # =============================================================================

    def publish(self) -> Any:
        """
        Publish the form.
        
        Returns:
            Response from the API publish call
        """
        return self._service.forms().setPublishSettings(
            formId=self.id, 
            body=self._getPublishSettings()
        ).execute()

    def create(self) -> Self:
        """
        Create a new form.
        
        Returns:
            Self for method chaining
        """
        self._values = self._service.forms().create(
            body=self._getNewBody(), 
            unpublished=False
        ).execute()
        return self
    
    def update(self, sleep=None) -> dict:
        """
        Update the form with current properties.
        
        Returns:
            Response from the API update call
        """
        form = None
        for updatedBody in self._getUpdatesBodies():
            form = self._service.forms().batchUpdate(
                formId=self.id, 
                body=updatedBody
            ).execute()
            if sleep:
                _sleep(sleep)
        return form
    
    def empty(self) -> None:
        """
        Empty the form by clearing all items.
        
        This method removes all items from the form.
        """
        while True:
            try:
                self._service.forms().batchUpdate(
                    formId=self.id, 
                    body={
                        'requests': [
                            {
                                'deleteItem': {
                                    'location': {
                                        'index': 0
                                    }
                                }
                            }
                        ],
                        'includeFormInResponse': True
                    }
                ).execute()
            except HttpError as e:
                break
    
    # =============================================================================
    # Question Item Methods
    # =============================================================================

    @appendNewItem
    def newTextQuestionItem(
            self, 
            id: str, 
            title: str, 
            description: str | None = None, 
            required: bool = True, 
            shortAnswer: bool = True) -> Self:
        """
        Create a new text question item.
        
        Args:
            id: Question ID
            title: Question title
            description: Question description
            required: Whether the question is required
            shortAnswer: Whether it's a short answer or paragraph
            
        Returns:
            Self for method chaining
        """
        return self._newQuestionItem(
            id, 
            title, 
            description, 
            required,
            'textQuestion',
            {
                'paragraph': not shortAnswer,
            }
        )
    
    @appendNewItem
    def newDateQuestionItem(
            self, 
            id: str, 
            title: str, 
            description: str | None = None, 
            required: bool = True, 
            includeTime: bool = False, 
            includeYear: bool = True) -> Self:
        """
        Create a new date question item.
        
        Args:
            id: Question ID
            title: Question title
            description: Question description
            required: Whether the question is required
            includeTime: Whether to include time selection
            includeYear: Whether to include year selection
            
        Returns:
            Self for method chaining
        """
        return self._newQuestionItem(
            id,
            title,
            description,
            required,
            'dateQuestion',
            {
                'includeTime': includeTime,
                'includeYear': includeYear,
            }
        )
    
    @appendNewItem
    def newTimeQuestionItem(
            self, 
            id: str, 
            title: str, 
            description: str | None = None, 
            required: bool = True, 
            duration: bool = False) -> Self:
        """
        Create a new time question item.
        
        Args:
            id: Question ID
            title: Question title
            description: Question description
            required: Whether the question is required
            duration: Whether it's a duration input
            
        Returns:
            Self for method chaining
        """
        return self._newQuestionItem(
            id, 
            title, 
            description, 
            required,
            'timeQuestion',
            {
                'duration': duration,
            }
        )
    
    @appendNewItem
    def newPageBreakItem(
        self, 
        id: str, 
        title: str, 
        description: str | None = None) -> Self:
        """
        Create a new page break item.
        
        Args:
            id: Item ID
            title: Item title
            description: Item description

        Returns:
            Self for method chaining
        """
        return self._newItem(
            id,
            title,
            description,
            'pageBreakItem',
            {},
        )
    
    @appendNewItem
    def newChoiceQuestionItem(
            self, 
            id: str, 
            title: str, 
            description: str | None = None, 
            required: bool = True, 
            choiceType: str = 'DROP_DOWN', 
            options: list[Any | str] | None = None, 
            shuffle: bool = False) -> Self:
        """
        Create a new choice question item.
        
        Args:
            id: Question ID
            title: Question title
            description: Question description
            required: Whether the question is required
            choiceType: Type of choice (RADIO, CHECKBOX, DROP_DOWN)
            options: List of choice options
            shuffle: Whether to shuffle the options
            
        Returns:
            Self for method chaining
        """
        if options is None:
            raise ValueError('Options cannot be None')

        if all(isinstance(opt, str) for opt in options):
            options = [{'value': opt} for opt in options]
        elif all(isinstance(opt, self.Option) for opt in options):
            options = [opt.get() for opt in options]
        else:
            raise TypeError(
                'Options in GoogleForm.newChoiceQuestionsItem method must be '
                'a list of GoogleForm.Option or strings')
        return self._newQuestionItem(
            id,
            title,
            description,
            required,
            'choiceQuestion',
            {
                'type': choiceType,
                'options': options,
                'shuffle': shuffle
            }
        )

    # =============================================================================
    # Private Methods
    # =============================================================================

    def _getForm(self) -> dict:
        """
        Retrieve the form data from the API.
        
        Returns:
            Dictionary containing form data
        """
        return self._service.forms().get(formId=self.id).execute()
    
    def _getNewBody(self) -> dict[str, dict]:
        """
        Get the body for creating a new form.
        
        Returns:
            Dictionary containing new form structure
        """
        return {
            "info": {
                "title": self.title,
                "documentTitle": self.documentTitle,
            }
        }
    
    def _getUpdateMask(self, dictionary: dict) -> str:
        """
        Generate update mask from dictionary keys.
        
        Args:
            dictionary: Dictionary to generate mask from
            
        Returns:
            Comma-separated string of keys
        """
        return ', '.join(list(dictionary.keys()))
       
    def _getPublishSettings(self) -> dict:
        """
        Get the publish settings for the form.
        
        Returns:
            Dictionary containing publish settings
        """
        return {
            'publishSettings': {
                'publishState': {
                    'isPublished': True,
                    'isAcceptingResponses': True,
                }
            },
            'updateMask': '*',
        }
    
    def _getUpdatesBodies(self) -> list[dict]:
        """
        Get the body content for form updates.
        
        Returns:
            List of dictionaries formatted for API update calls
        """
        bodies = list()
        bodies.append(
            self._getUpdateBody(
                {
                    'updateFormInfo': self._getInfoUpdateBody()         
                }       
            )
        )
        if self.has('settings'):
            bodies.append(
                self._getUpdateBody(
                    {
                        'updateSettings': {
                            'settings': self._get('settings'),
                            'updateMask': self._getUpdateMask(
                                self._get('settings')
                            ),
                        }
                    }  
                )
            )
        location = 0
        for i in range(len(self._newItems) - 1, -1, -1):
            bodies.append(
                self._getUpdateBody(
                    {
                        'createItem': {
                            'item': self._newItems[i],
                            'location': {
                                'index': location
                            },
                        }
                    }
                )
            )
        return bodies

    def _getUpdateBody(self, update: dict) -> dict:
        """
        Create an update body wrapper.
        
        Args:
            update: Update content to wrap
            
        Returns:
            Dictionary containing wrapped update
        """
        return {
            'includeFormInResponse': True,
            'requests': [update]
        }

    def _newItem(
        self, 
        id: str, 
        title: str, 
        description: str | None, 
        itemType: str, 
        body: dict) -> Self:
        """
        Create a new item of the specified type.
        
        Args:
            id: Item ID
            title: Item title
            description: Item description
            itemType: Type of item to create
            body: Item body content
            
        Returns:
            Self for method chaining
        """
        return {
            'itemId': id,
            'title': title,
            'description': description,
            itemType: body
        }

    def _newQuestionItem(
            self, 
            id: str, 
            title: str, 
            description: str | None, 
            required: bool, 
            questionType: str, 
            body: dict) -> Self:
        """
        Create a new question item.
        
        Args:
            id: Question ID
            title: Question title
            description: Question description
            required: Whether the question is required
            questionType: Type of question
            body: Question body content
            
        Returns:
            Self for method chaining
        """
        return self._newItem(
            id,
            title,
            description,
            'questionItem',
            body = {
                'question': {
                    'questionId': id,
                    'required': required,
                    questionType: body,
                }
            }
        )
    
    def _parseOptions(
        self, 
        options: list[Any | str]) -> tuple[list[dict], list[dict]]:
        """
        Parse options for choice questions.
        
        Args:
            options: List of options to parse
            
        Returns:
            Tuple containing new items and items to update with after initial update
        """
        newItems = list()
        updateItems = list()
        for option in options:
            newItems.append({'value': option.value})
            if option.hasActions:
                updateItems.append(option.get())
        return newItems, updateItems

    def _getInfoUpdateBody(self) -> dict:
        """
        Get the info update body for form updates.
        
        Returns:
            Dictionary containing info update structure
        """
        body = {
            'info': {
                'title': self.title
            },
            'updateMask': 'title'
        }
        if self.description:
            body['info']['description'] = self.description
            body['updateMask'] = 'title, description'
        return body
    
    def _nextLocation(self) -> str:
        """
        Get the next location for new items.
        
        Returns:
            A string representing the next location
        """
        return str(len(self._newItems) + 1) # Incremental location based on current items count

    # =============================================================================
    # Magic Methods
    # =============================================================================

    def __getattr__(self, attr: str) -> Any:
        """
        Handle getting special attributes.
        
        Args:
            attr: Name of the attribute to get
            
        Returns:
            Attribute value
        """
        if attr == 'name':
            return self.title
        return super().__getattr__(attr)
    
    def __setattr__(self, attr: str, value: Any) -> None:
        """
        Handle setting special attributes.
        
        Args:
            attr: Name of the attribute to set
            value: New value for the attribute
        """
        if attr == 'name':
            self.title = value
        else:
            super().__setattr__(attr, value)