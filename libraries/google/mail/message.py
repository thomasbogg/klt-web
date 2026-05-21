import base64
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from libraries.google.connect import GoogleAPIService
from libraries.dates import dates
from libraries.translator.deepl import Deepl
from libraries.utils import Object, logerror, logtest, sublog, toList


class GoogleMailMessage(Object):
    """
    Represents a Gmail message with functionality for composition, reading, and sending.
    
    This class provides a comprehensive interface to work with individual Gmail messages,
    including creating messages with formatted content, adding attachments, and
    interacting with the Gmail API.
    """

    class Greeting(Object):
        """
        Manages email greeting formats with options for formal/informal styles.
        
        Provides customization of email greeting text and format (HTML or plain text).
        """
        
        def __init__(
                self, 
                name: str | None = None, 
                formal: bool = False, 
                isHTML: bool = True,
                translator: Deepl | Any = None) -> None:
            """
            Initialize a new email greeting.
            
            Args:
                name: Name of the recipient
                formal: Whether to use formal greeting style
                isHTML: Whether to format greeting as HTML
            """
            super().__init__(name=name)
            self._formal = formal
            self._isHTML = isHTML
            self._translator = translator

        @property
        def greeting(self) -> str:
            """
            Get the formatted greeting text based on current settings.
            
            Returns:
                str: Formatted greeting text
            """
            if self._formal: 
                greeting = 'Dear'
            else:
                greeting = 'Hello,' 

            if self._translator:
                greeting = self._translator.translate(greeting)
            
            value = f'{greeting} {self.name},'

            if self._isHTML: 
                return f'<p>{value}</p>'
            return f'{value}\n'

        def html(self) -> 'GoogleMailMessage.Greeting':
            """
            Set greeting format to HTML.
            
            Returns:
                Greeting: Self for method chaining
            """
            self._isHTML = True
            return self

        def plain(self) -> 'GoogleMailMessage.Greeting':
            """
            Set greeting format to plain text.
            
            Returns:
                Greeting: Self for method chaining
            """
            self._isHTML = False
            return self

        def informal(self) -> 'GoogleMailMessage.Greeting':
            """
            Use informal greeting style.
            
            Returns:
                Greeting: Self for method chaining
            """
            self._formal = False
            return self
        
        def formal(self) -> 'GoogleMailMessage.Greeting':
            """
            Use formal greeting style.
            
            Returns:
                Greeting: Self for method chaining
            """
            self._formal = True
            return self
        
        @property
        def translator(self) -> str:
            return self._translator
        
        @translator.setter
        def translator(self, value: Deepl | Any) -> None:
            self._translator = value
        
    class Body(Object):
        """
        Manages the content of an email body with support for various formatting elements.
        
        Provides methods to build structured email content including paragraphs,
        tables, links, and sections with styling options.
        """
        
        def __init__(
                self, 
                load: dict = None, 
                isHTML: bool = True, 
                isDraft: bool = False,
                translator: Deepl | Any = None) -> None:
            """
            Initialize a new email body.
            
            Args:
                load: Dictionary of attributes to load
                isHTML: Whether to format body as HTML
                isDraft: Whether this is a draft message
            """
            super().__init__(load=load or {})
            self._isDraft = isDraft
            self._isHTML = isHTML
            self._body = list()
            self._section_count = 0
            self._translator = translator

        # =============================================================================
        # Format Methods
        # =============================================================================

        def html(self) -> 'GoogleMailMessage.Body':
            """
            Set body format to HTML.
            
            Returns:
                Body: Self for method chaining
            """
            self._isHTML = True
            return self

        def plain(self) -> 'GoogleMailMessage.Body':
            """
            Set body format to plain text.
            
            Returns:
                Body: Self for method chaining
            """
            self._isHTML = False
            return self   
        
        # =============================================================================
        # Properties
        # =============================================================================
        
        @property
        def hasContent(self) -> bool:
            """Check if the body has any content."""
            return bool(self._body)

        @property
        def isHTML(self) -> bool:
            """Check if the body is formatted as HTML."""
            return self._isHTML

        @property
        def body(self) -> str:
            """
            Get the formatted body content.
            
            Returns:
                str: Complete formatted body content
            """
            if not self._isHTML:
                return '\n\n'.join([segment['content'] for segment in self._body])
            body = ''
            for segment in self._body:
                body += getattr(self, segment['type'])(segment['content'], **segment['styles'])
            self._section_count = 0
            return body
        
        @property
        def translator(self) -> str:
            return self._translator
        
        @translator.setter
        def translator(self, value) -> None:
            self._translator = value

        # =============================================================================
        # Content Methods
        # =============================================================================

        def set(self, load: dict | None) -> 'GoogleMailMessage.Body':
            """
            Set body content from API response data.
            
            Args:
                load: API response data containing message content
                
            Returns:
                Body: Self for method chaining
            """
            if not load:
                return self

            try: 
                data = load['payload']['parts'][0]['body']['data']
            except KeyError: 
                try:
                    data = load['payload']['body']['data']
                except Exception as e:
                    return (f'No data found in email body. Threw error: {e}')

            body = base64.urlsafe_b64decode(data).decode()
            paragraphs = self._parse_body_to_paragraphs(body)

            for paragraph in paragraphs:
                if '*' in paragraph[0]:
                    paragraph = paragraph.replace('*', '')
                    self._set(paragraph, type='_paragraph', italic=True)
                    continue

                self._set(paragraph, type='_paragraph')
            
            return self

        def paragraph(self, *text, **styles) -> 'GoogleMailMessage.Body':
            """
            Add a paragraph to the body.
            
            Args:
                *text: Text content for the paragraph
                **styles: Formatting styles to apply
                
            Returns:
                Body: Self for method chaining
            """
            text = ' '.join(toList(text))
            return self._set(text, type='_paragraph', **styles)

        def table(self, rows: list[list]) -> 'GoogleMailMessage.Body':
            """
            Add a table to the body.
            
            Args:
                rows: List of row data, where each row is a list of cell values
                
            Returns:
                Body: Self for method chaining
            """
            return self._set(rows, type='_table')

        def link(self, *link, **styles) -> 'GoogleMailMessage.Body':
            """
            Add a hyperlink to the body.
            
            Args:
                *link: URL and optional display text
                **styles: Formatting styles to apply
                
            Returns:
                Body: Self for method chaining
            """
            return self._set(link, type='_link', **styles)

        def separation(self) -> 'GoogleMailMessage.Body':
            """
            Add a horizontal separator to the body.
            
            Returns:
                Body: Self for method chaining
            """
            return self._set(None, type='_separation')

        def section(self, *text) -> 'GoogleMailMessage.Body':
            """
            Add a numbered section heading.
            
            Args:
                *text: Text content for the section heading
                
            Returns:
                Body: Self for method chaining
            """
            text = ' '.join(list(text))
            return self._set(text, type='_section', bold=True)

        def title(self, *text) -> 'GoogleMailMessage.Body':
            """
            Add a centered title with bold and underline formatting.
            
            Args:
                *text: Text content for the title
                
            Returns:
                Body: Self for method chaining
            """
            return self.paragraph(*text, bold=True, underlined=True, center=True)
        
        # =============================================================================
        # Internal Methods
        # =============================================================================
        
        def _set(self, content: any, type: str, **styles) -> 'GoogleMailMessage.Body':
            """
            Add a content segment to the body.
            
            Args:
                content: Content to add
                type: Type of content segment
                **styles: Formatting styles to apply
                
            Returns:
                Body: Self for method chaining
            """
            self._body.append(
                {
                    'content': content,
                    'type': type,
                    'styles': styles,
                }
            )
            return self

        def _paragraph(self, content: str | list, **styles) -> str:
            """
            Format paragraph content.
            
            Args:
                content: Text content for the paragraph
                **styles: Formatting styles to apply
                
            Returns:
                str: Formatted paragraph
            """
            if not isinstance(content, str): 
                content = ' '.join(toList(content))

            if self._translator:
                content = self._translator.translate(content)

            if not self._isHTML: 
                return f'\n{content}\n'
                return f'\n{self._remove_tags(content)}\n' ## Probably deprecated as content is processed only in final call

            styles = self._sort_styles(**styles)
            return f'<p style="line-height: 1.8; {styles}">{content}</p>'

        def _table(self, rows_of_data: list[list], **kwargs) -> str:
            """
            Format table content.
            
            Args:
                rows_of_data: List of row data, where each row is a list of cell values
                **kwargs: Additional formatting options
                
            Returns:
                str: Formatted HTML table
            """
            content = '<table><tbody>'
            for row in rows_of_data:
                content += '<tr>'

                for data in row : 
                    content += '<td style="padding:3px">'
                    if self._translator:
                        data = self._translator.translate(data)
                    content += f'{data}'
                    content += '</td>'

                content += '</tr>'

            content += '</tbody></table>'

            return content

        def _link(self, link: str | list, **styles) -> str:
            """
            Format hyperlink content.
            
            Args:
                link: URL and optional display text
                **styles: Formatting styles to apply
                
            Returns:
                str: Formatted hyperlink
            """
            if not isinstance(link, str):
                if len(link) == 2:
                    link, text = link
                    if self._translator:
                        text = self._translator.translate(text)
                else:
                    link, text = link[0], link[0]
                    
            if not self._isHTML:
                return self._paragraph(link)
            return self._paragraph(f'<a href="{link}">{text}</a>', **styles)

        def _separation(self, *args, **kwargs) -> str:
            """
            Format horizontal separator.
            
            Returns:
                str: Formatted separator
            """
            return self._paragraph('_____________________', styles=None)
        
        def _section(self, content: str, **styles) -> str:
            """
            Format section heading.
            
            Args:
                content: Text content for the section heading
                **styles: Formatting styles to apply
                
            Returns:
                str: Formatted section heading
            """
            self._section_count += 1
            return self._paragraph(f'{self._section_count}) {content}', **styles)
        
        def _sort_styles(self, **styles) -> str:
            """
            Convert styling attributes to CSS.
            
            Args:
                **styles: Styling attributes
                
            Returns:
                str: CSS style string
            """
            if not styles:
                return ''

            sses = list()
            if styles.get('bold', False):
                sses.append('font-weight: bold;')
        
            if styles.get('underlined', False): 
                sses.append('text-decoration: underline;')
        
            if styles.get('center', False) or styles.get('centre', False):
                sses.append('text-align: center;')
        
            if styles.get('indent', False): 
                sses.append(f'text-indent: {styles["indent"]}px')

            return ' '.join(sses)

        def _remove_tags(self, text: str) -> str:
            """
            Remove HTML tags from text.
            
            Args:
                text: Text containing HTML tags
                
            Returns:
                str: Text with HTML tags removed
            """
            tags = ('<b>', '</b>', '<u>', '</u>', '<i>', '</i>')
            for tag in tags: 
                text = text.replace(tag, '')
            return text

        def _parse_body_to_paragraphs(self, body: str) -> list[str]:
            """
            Parse raw message body into paragraphs.
            
            Args:
                body: Raw message body text
                
            Returns:
                list[str]: List of paragraphs
            """
            paragraphs = list()
            split_paragraphs = body.split('\r\n\r\n')

            for paragraph in split_paragraphs:
                if 'Kind regards,' in paragraph and len(split_paragraphs) > 1:
                    
                    if not self._isDraft:
                        break

                    signature_paragraphs = paragraph.split('\r\n')
                    for sig in signature_paragraphs:
                        stripped_sig = sig.strip()
                        
                        if stripped_sig:
                            paragraphs.append(stripped_sig)
                else:
                    stripped = paragraph.replace('\r\n', '').strip()
                    if stripped:
                        paragraphs.append(stripped)

            return paragraphs
        
        def __str__(self) -> str:
            """
            Get plain text representation of the body.
            
            Returns:
                str: Plain text body content
            """
            html = self._isHTML
            body = self.plain().body
            self._isHTML = html
            return body
        
    class Signature(Object):
        """
        Manages email signature with support for contact details.
        
        Creates formatted email signatures with sender name and optional details.
        """
        
        def __init__(
                self, 
                name: str | None = None, 
                details: list | None = None,
                translator: Deepl | Any = None) -> None:
            """
            Initialize a new email signature.
            
            Args:
                name: Name of the sender
                details: List of additional details for the signature
            """
            super().__init__(name=name)
            self.details = details
            self._translator = translator

        @property
        def signature(self) -> str:
            """
            Get the formatted signature text.
            
            Returns:
                str: HTML formatted signature
            """
            content = '<p style="line-height: .1;">&nbsp;</p>'
            content += '<p>Kind regards,</p>'
            content += f'<p>{self.name}</p>'

            if not self.details: 
                return content

            content += '<span style="font-size:90%;">'
            content += '<p>_______________________</p>'

            for i in range(0, len(self.details)):
                detail = self.details[i]

                if i == 0 or i % 2 == 0 : 
                    content += f'<p><b>{detail}</b></p>'
                else : 
                    content += f'<p><i>{detail}</i></p>'

            content += '</span>'
            return content

        @property
        def details(self) -> list | None:
            """Get additional details for the signature."""
            return self._get('details')
        
        @details.setter
        def details(self, value: list | None) -> None:
            """Set additional details for the signature."""
            self._set('details', value)
        
        @property
        def translator(self) -> str:
            return self._translator
        
        @translator.setter
        def translator(self, value) -> None:
            self._translator = value

    # =============================================================================
    # Initialization
    # =============================================================================

    def __init__(
            self, 
            connection: GoogleAPIService, 
            load: dict = None, 
            id: str = None, 
            isDraft: bool = False, 
            translator : str = None,
            TEST: bool = False) -> None:
        """
        Initialize a new GoogleMailMessage instance.
        
        Args:
            connection: GoogleAPIService connection for API access
            load: Message data to load from API response
            id: Message ID
            isDraft: Whether this is a draft message
            TEST: If True, operations won't affect production data
        """
        super().__init__(id=id, TEST=TEST)
        self._connection = connection
        self._service = connection.connection if connection else None
        self._isHTML = True
        self._formal = False
        self._isDraft = isDraft
        self._translator = translator
     
        if load:
            self.set(load)
            if isDraft:
                self._to = list()
                self._cc = list()
                self._bcc = list()
                self._attachments = list()
        else:
            self._to = list()
            self._cc = list()
            self._bcc = list()
            self._subject = ''
            self._attachments = list()
            self._sender = None
            self._date = None
    
    # =============================================================================
    # Connection Properties
    # =============================================================================

    @property
    def connection(self) -> GoogleAPIService | None:
        """Get the API connection associated with this message."""
        return self._connection
    
    @connection.setter
    def connection(self, value: GoogleAPIService) -> None:
        """Set the API connection for this message."""
        self._connection = value
        self._service = value.connection if value else None
    
    @property
    def hasConnection(self) -> bool:
        """Check if there is an active API connection."""
        return self._connection is not None

    # =============================================================================
    # Message Properties
    # =============================================================================
    
    @property
    def isDraft(self) -> bool:
        """Check if this is a draft message."""
        return self._isDraft

    @property
    def hasContent(self) -> bool:
        """Check if the message body has content."""
        return self.body.hasContent

    @property
    def isHTML(self) -> bool:
        """Check if the message is formatted as HTML."""
        return self.body.isHTML
    
    @property
    def translator(self) -> str | None:
        """Get the target language of the content."""
        return self._translator
    
    @translator.setter
    def translator(self, value) -> None:
        """Set the target language of the content."""
        self._translator = value
        self.body.translator = value
        self.greeting.translator = value
        self.signature.translator = value
    
    @property
    def body(self) -> 'GoogleMailMessage.Body':
        """Get the message body object."""
        if not hasattr(self, '_body'):
            self._body = GoogleMailMessage.Body(
                                            isHTML=self._isHTML, 
                                            isDraft=self._isDraft,
                                            translator=self._translator
            )
        return self._body

    @property
    def greeting(self) -> 'GoogleMailMessage.Greeting':
        """Get the message greeting object."""
        if not hasattr(self, '_greeting'):
            self._greeting = GoogleMailMessage.Greeting(translator=self._translator)
        return self._greeting
    
    @property
    def signature(self) -> 'GoogleMailMessage.Signature':
        """Get the message signature object."""
        if not hasattr(self, '_signature'):
            self._signature = GoogleMailMessage.Signature(translator=self._translator)
        return self._signature

    @property
    def sender(self) -> str | None:
        """Get the sender email address."""
        return self._sender
    
    @sender.setter
    def sender(self, value: str) -> None:
        """Set the sender email address."""
        self._sender = value
    
    @property
    def date(self) -> str | None:
        """Get the message date."""
        return self._date

    @date.setter
    def date(self, value: str) -> None:
        """Set the message date."""
        self._date = value

    @property
    def to(self) -> str:
        """Get the recipient email addresses as a comma-separated string."""
        return ', '.join(self._to)

    @to.setter
    def to(self, value: str) -> None:
        """Add a recipient email address."""
        if value is None:
            return
        self._to.append(value)

    @property
    def cc(self) -> str:
        """Get the CC email addresses as a comma-separated string."""
        if not hasattr(self, '_cc'):
            self._cc = list()
        return ', '.join(self._cc)
    
    @cc.setter
    def cc(self, value: str) -> None:
        """Add a CC email address."""
        self._cc.append(value)

    @property
    def bcc(self) -> str:
        """Get the BCC email addresses as a comma-separated string."""
        if not hasattr(self, '_bcc'):
            self._bcc = list()
        return ', '.join(self._bcc)
    
    @bcc.setter
    def bcc(self, value: str) -> None:
        """Add a BCC email address."""
        self._bcc.append(value)

    @property
    def subject(self) -> str:
        """Get the message subject."""
        return self._subject

    @subject.setter
    def subject(self, value: str) -> None:
        """Set the message subject."""
        self._subject = value

    @property
    def attachments(self) -> list[str]:
        """Get the list of attachment file paths."""
        return self._attachments
    
    @attachments.setter
    def attachments(self, value: str | list[str]) -> None:
        """Add file attachments to the message."""
        self._attachments += toList(value)

    # =============================================================================
    # Action Methods
    # =============================================================================

    def delete(self) -> 'GoogleMailMessage':
        """
        Delete (trash) this message.
        
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        if self.TEST: 
            return logtest(f'delete message of {self._id}, {self._subject}')
        self._service.users().messages().trash(userId='me', id=self.id).execute()
        return self

    def send(self, verbose: bool = True) -> 'GoogleMailMessage':
        """
        Send this message.
        
        Returns:
            GoogleMailMessage: Self for method chaining
            
        Raises:
            RuntimeError: If message is missing sender, recipient, or body
        """
        if self._sender is None: 
            return logerror('Got no sender for email. RETURNING.')
        if not self._to: 
            return logerror('Got no recipient for email. RETURNING.')
        if self.TEST and not self.subject: 
            sublog('Got no subject for email. BUT CONTINUING ...')
        if not self.body.hasContent: 
            return logerror('Got no body for email. RETURNING.')        
        
        if self._isHTML: 
            message = MIMEMultipart()
        else: 
            message = EmailMessage()

        message['From'] = self.sender
        message['To'] = self.to if not self.TEST else self.sender
        message['Cc'] = self.cc
        message['Bcc'] = self.bcc
        message['Subject'] = self.subject
        
        body = self.greeting.greeting
        body += self.body.body
        
        if self._isHTML:
            if not self._isDraft: 
                body += self.signature.signature

            message.attach(MIMEText(body, 'html'))
        else:
            message.set_content(body)
        
        self._add_attachments(message)
        
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        encoded_body = {'raw': encoded_message}
        
        self._service.users().messages().send(userId='me', body=encoded_body).execute()
        #print(f'sending to {message["From"]}')
        if verbose:
            self._log_send()
        self._reset_recipients()
        return self

    def set(self, load: dict = None) -> 'GoogleMailMessage':
        """
        Set message properties from API response data.
        
        Args:
            load: API response data containing message details
            
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        if not load: 
            return self
        self._set_header(load['payload']['headers'])
        self.body.set(load)
        return self
    
    def clear_recipients(self) -> 'GoogleMailMessage':
        """
        Clear all recipient lists (To, Cc, Bcc).
        
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        return self._reset_recipients()

    # =============================================================================
    # Internal Methods
    # =============================================================================

    def _add_attachments(self, message: MIMEMultipart | EmailMessage) -> 'GoogleMailMessage':
        """
        Add attachments to the message.
        
        Args:
            message: Email message object to add attachments to
            
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        if not self._attachments:
            return self

        attachment = self._attachments.pop(0)
        filename = attachment.split('/')[-1]

        with open(attachment, 'rb') as file: 
            if not self._isHTML:
                message.add_attachment(
                                    file.read(), 
                                    maintype='text', 
                                    subtype='plain', 
                                    filename=filename
                )
            else:
                part = MIMEApplication(file.read(), Name=filename)
                part['Content-Disposition'] = f'attachment; filename={filename}'
                message.attach(part)     
        
        return self._add_attachments(message)

    def _set_header(self, header_items: list[dict], previous_value: str = None) -> 'GoogleMailMessage':
        """
        Parse header information from API response.
        
        Args:
            header_items: List of header items from API response
            previous_value: Value from previous iteration for matching fields
            
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        if len(header_items) == 0: 
            return self
        
        item = header_items.pop(0)
        for val in item.values():
        
            if previous_value == 'To':
                self._to = val.split(', ')
            elif previous_value == 'Cc':
                self._cc = val.split(', ')
            elif previous_value == 'Bcc':
                self._bcc = val.split(', ')        
            elif previous_value == 'Subject':
                self._subject = val        
            elif previous_value == 'Date':
                day, month, year = val.split(' ')[1:4]
                self._date = dates.date(year=year, month=month, day=day)
            elif previous_value == 'From':
                self._sender = val
        
            previous_value = val
        
        return self._set_header(header_items, previous_value)

    def _log_send(self) -> 'GoogleMailMessage':
        """
        Log information about the sent message.
        
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        if not self.subject: 
            self.subject = 'NO SUBJECT'
        elif len(self.subject) > 50: 
            self.subject = self.subject[:50]
        
        sublog(f'SENT "{self.subject}..." TO {self.to}')
        return self
    
    def _reset_recipients(self) -> 'GoogleMailMessage':
        """
        Reset message recipient lists.
        
        Returns:
            GoogleMailMessage: Self for method chaining
        """
        self._to = list()
        self._cc = list()
        self._bcc = list()
        return self

    def __str__(self) -> str:
        """
        Get string representation of the message.
        
        Returns:
            str: Formatted string with message details
        """
        string = '\n\n\t___EMAIL MESSAGE___'        
        string += f'\n\tSubject: {self.subject}'        
        string += f'\n\tFrom: {self.sender}'        
        string += f'\n\tTo: {self.to}'        
        string += f'\n\tDate: {self.date}'        
        string += f'\n\tCc: {self.cc}'        
        string += f'\n\tBcc: {self.bcc}'
        string += f'\n\tBody:\n\n{self.body}'        
        return string