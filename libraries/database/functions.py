from libraries.dates import dates
from libraries.utils import logerror


def sort_value_for_database(value, dataType):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, str):
        if dataType in ('text', 'blob'):
            return f'"{value}"'
        if dataType == 'integer':
            return int(value)
        if dataType == 'real':
            return float(value)
    if dates.isDatetimeDate(value):
        return f'"{value}"'
    if dates.isDatetimeDatetime(value):
        return f'"{value}"'
    if dates.isDatetimeTime(value):
        return dates.toIsoFormat(value)
    return value


def sort_values_for_database(values, dataType):
    sorted_values = [str(sort_value_for_database(value, dataType)) for value in values]
    return ', '.join(sorted_values)