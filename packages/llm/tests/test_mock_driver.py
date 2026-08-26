from llm.drivers.mock import MockLLM


def test_chat_cites_every_excerpt_deterministically():
    llm = MockLLM()
    messages = [
        {"role": "system", "content": "Отвечай только по выдержкам, цитируй [n]."},
        {"role": "user", "content": (
            "Контекст:\n"
            "[1] Шато Вымысел Каберне — плотное сухое красное с танинами.\n"
            "[2] Белые Пески Совиньон Блан — свежее сухое белое с цитрусом.\n"
            "Вопрос: что выбрать к стейку?"
        )},
    ]
    answer1 = llm.chat(messages)
    answer2 = llm.chat(messages)
    assert answer1 == answer2, "mock должен быть детерминирован"
    assert "[1]" in answer1
    assert "[2]" in answer1


def test_chat_without_excerpts_has_no_citation_markers():
    llm = MockLLM()
    messages = [{"role": "user", "content": "Просто вопрос без контекста"}]
    answer = llm.chat(messages)
    assert "[1]" not in answer and "[" not in answer


def test_chat_stream_yields_same_text_as_chat():
    llm = MockLLM()
    messages = [{"role": "user", "content": "[1] Тестовая выдержка про вино."}]
    streamed = "".join(llm.chat_stream(messages))
    assert streamed == llm.chat(messages)


def test_json_mode_wraps_answer_in_json_object():
    import json

    llm = MockLLM()
    messages = [{"role": "user", "content": "[1] Выдержка."}]
    raw = llm.chat(messages, json_mode=True)
    parsed = json.loads(raw)
    assert "answer" in parsed
    assert "[1]" in parsed["answer"]


def test_excerpts_found_across_multiple_messages():
    llm = MockLLM()
    messages = [
        {"role": "system", "content": "[1] Первая выдержка в системном сообщении."},
        {"role": "user", "content": "[2] Вторая выдержка в пользовательском."},
    ]
    answer = llm.chat(messages)
    assert "[1]" in answer
    assert "[2]" in answer
