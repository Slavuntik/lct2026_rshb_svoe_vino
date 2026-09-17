from winescan.vision.detector import Detection, choose_main_package

SIZE = (1200, 1600)


def test_group_box_loses_to_central_bottle():
    # как на фото Массандры: огромная рамка вокруг нескольких бутылок против центральной бутылки
    group = Detection((39, 14, 1143, 1413), 0.20, "a wine bottle")
    left = Detection((0, 334, 296, 1317), 0.32, "a wine bottle")
    right = Detection((787, 226, 1200, 1518), 0.37, "a wine bottle")
    center = Detection((372, 300, 830, 1489), 0.27, "a wine bottle")

    assert choose_main_package([group, left, right, center], SIZE) == center


def test_single_bottle_ignores_tiny_background_detections():
    bottle = Detection((130, 1, 1082, 1399), 0.33, "a wine bottle")
    tiny = [Detection((841, 332, 887, 393), 0.126, "a wine bottle"), Detection((706, 340, 768, 393), 0.125, "a wine bottle")]

    assert choose_main_package([bottle, *tiny], SIZE) == bottle


def test_empty_detections():
    assert choose_main_package([], SIZE) is None
