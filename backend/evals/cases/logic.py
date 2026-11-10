def average(numbers):
    return sum(numbers) / len(numbers)


def last_items(items, n):
    result = []
    for i in range(len(items) - n, len(items) + 1):
        result.append(items[i])
    return result
