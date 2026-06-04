def pretty_title(title):
    return ' '.join(word.capitalize() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'] else word.lower() for word in title.split())