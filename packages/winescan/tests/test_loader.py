from winescan.catalog.loader import load_catalog_csv

HEADER = "Название вина,Категория,Цвет,Регион,Сорт винограда,Описание,Винодельня,Slug,Название фото\n"


def test_load_dedups_rows_and_strips_whitespace(tmp_path):
    csv = tmp_path / "catalog.csv"
    csv.write_text(
        HEADER
        + ' Алиготе,Белое,Золотистый,Крым,Алиготе,"Описание\n",Коммуналка,aligote,a.webp\n'
        + ' Алиготе,Белое,Золотистый,Крым,Алиготе,"Описание\n",Коммуналка,aligote,a.webp\n'
        + 'Алиготе,Белое,Золотистый,Крым,Алиготе,Описание,Коммуналка,aligote,a.webp\n'
        + 'Купаж,Красное,Рубиновый,Кубань,"Мерло, Саперави",Текст,Фанагория,kupazh,b.webp\n',
        encoding="utf-8",
    )
    df, stats = load_catalog_csv(csv)

    assert (stats.rows_raw, stats.rows_after_exact_dedup, stats.rows_after_strip_dedup) == (4, 3, 2)
    assert stats.unique_slugs == 2 and stats.conflicting_slugs == []
    assert df.loc[df.slug == "aligote", "name"].item() == "Алиготе"
    assert df.loc[df.slug == "kupazh", "grapes_list"].item() == ["Мерло", "Саперави"]


def test_conflicting_slug_keeps_first_row_and_is_reported(tmp_path):
    csv = tmp_path / "catalog.csv"
    csv.write_text(
        HEADER
        + "Вино А,Белое,Цвет,Крым,Сорт,Текст,Винодельня,same-slug,a.webp\n"
        + "Вино Б,Белое,Цвет,Крым,Сорт,Текст,Винодельня,same-slug,b.webp\n",
        encoding="utf-8",
    )
    df, stats = load_catalog_csv(csv)

    assert stats.conflicting_slugs == ["same-slug"]
    assert df["name"].tolist() == ["Вино А"]
