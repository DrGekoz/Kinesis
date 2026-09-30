from screeninfo import get_monitors


def get_screen_size():
    m = get_monitors()[0]
    return m.width, m.height
