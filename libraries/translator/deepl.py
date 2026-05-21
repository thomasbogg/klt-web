import deepl

from libraries.utils import (
    log, 
    Object,
    sublog 
)


class Deepl(Object):

    _targetLangs = { 
        'AR': 'Arabic', 'BG': 'Bulgarian', 'CS': 'Czech', 'DA': 'Danish', 
        'DE': 'German', 'EL': 'Greek', 'EN-GB': 'English (British)', 
        'EN-US': 'English (American)', 'ES': 'Spanish', 'ET': 'Estonian', 
        'FI': 'Finnish', 'FR': 'French', 'HE': 'Hebrew', 'HU': 'Hungarian', 
        'ID': 'Indonesian', 'IT': 'Italian', 'JA': 'Japanese', 'KO': 'Korean', 
        'LT': 'Lithuanian', 'LV': 'Latvian', 'NB': 'Norwegian', 'NL': 'Dutch', 
        'PL': 'Polish', 'PT-BR': 'Portuguese (Brazilian)', 
        'PT-PT': 'Portuguese (Portugal)', 'RO': 'Romanian', 'RU': 'Russian', 
        'SK': 'Slovak', 'SL': 'Slovenian', 'SV': 'Swedish', 'TH': 'Thai', 
        'TR': 'Turkish', 'UK': 'Ukrainian', 'VI': 'Vietnamese', 'ZH-HANS': 
        'Chinese (simplified)', 'ZH-HANT': 'Chinese (traditional)',
    }

    def __init__(
            self, 
            authKey: str = None, 
            targetLang: str = 'PT-PT', 
            **kwargs
    ) -> None:
        kwargs.update(
            {
                'authKey': authKey, 
                'targetLang': self.langExists(targetLang)
            }
        )
        super().__init__(load=kwargs)

    def translate(self, text: str = None) -> str:
        if not text:
            text = self._get('text')
      
        if not self._translator_is_set():
            self._set_translator()
            
        translated = self._translator.translate_text(
                                                text=text, 
                                                target_lang=self.targetLang)
        return str(translated)
  
    @property
    def authKey(self) -> str:
        return self._get('authKey')
    
    @authKey.setter
    def authKey(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError('AuthKey must be string type')
        
    @property
    def translator(self):
        return self._translator
    
    @property
    def translatorIsSet(self):
        return self._translator_is_set()
    
    @property
    def text(self) -> str:
        return self._get('text')
    
    @text.setter
    def text(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError('Text must be string type')
        self._set('text', value)

    @property
    def targetLang(self) -> str:
        return self._get('targetLang')
    
    @targetLang.setter
    def targetLang(self, value: str) -> None:
        value = self.langExists(value)
        self._set('targetLang', value)
    
    @property
    def allTargetLangs(self) -> dict:
        return self._targetLangs

    @property
    def currentUsage(self):
        if not self._translator_is_set():
            self._set_translator()
        usage = self._translator.get_usage().character
        return f'DEEPL FREE API: used {usage} characters'
    
    def _set_translator(self, authKey: str = None) -> 'Deepl':
        if authKey:
            self.authKey = authKey
        self._translator = deepl.Translator(auth_key=self.authKey)
        return self

    def _translator_is_set(self) -> bool:
        return hasattr(self, '_translator')
    
    @staticmethod
    def langExists(value):
        if value is None:
            return None
     
        available = Deepl._targetLangs
        maybe = None
        
        for key, desc in available.items():
            if key == value:
                return value
            if desc.lower() == value.lower():
                return key
            if value.lower() in desc.lower():
                maybe = value
     
        if maybe:
            return maybe
        raise ValueError(
            f'The given language "{value}" is not recognised for translation')
    
    @staticmethod
    def print(printer: callable = None) -> None:
        if printer:
            _log = printer.log
            _sublog = printer.sublog
        else:
            _log = log
            _sublog = sublog
        _log('The available codes of languages for translation are:')
        for k, v in Deepl._targetLangs.items():
            _sublog(f'{k}: {v}')