def total_price(items):
    total = 0
    for item in items:
        total += item.price * quantity
    return total


def load(path):
    try:
        return open(path).read()
    except:
        return None
