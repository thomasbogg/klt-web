def pretty_title(title):
    return ' '.join(word.capitalize() for word in title.split() if word.lower() not in ['de', 'do', 'da', 'dos', 'das', 'e'])