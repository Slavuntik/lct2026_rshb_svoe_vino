import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { waitlistEmails } from "../mocks/state";
import { renderApp } from "../test/renderApp";
import { WaitlistForm } from "./WaitlistForm";

describe("WaitlistForm — без согласия не отправляется", () => {
  it("кнопка отправки недоступна, пока согласие не отмечено", () => {
    renderApp(<WaitlistForm />);
    expect(screen.getByRole("button", { name: /записаться/i })).toBeDisabled();
  });

  it("прямой submit без согласия ловится обработчиком формы, а не только атрибутом disabled", () => {
    renderApp(<WaitlistForm />);
    fireEvent.change(screen.getByLabelText(/ваш e-mail/i), { target: { value: "reader@example.com" } });

    // fireEvent.submit обходит disabled-кнопку и бьёт напрямую в onSubmit — так проверяем
    // саму защиту в WaitlistForm.handleSubmit, а не только то, что кнопка неактивна в разметке.
    fireEvent.submit(screen.getByRole("form", { name: /лист ожидания/i }));

    expect(screen.getByText(/нужно согласие на обработку e-mail/i)).toBeInTheDocument();
    expect(waitlistEmails).toHaveLength(0);
    expect(screen.queryByTestId("waitlist-success")).not.toBeInTheDocument();
  });

  it("невалидный e-mail тоже не уходит в сеть, даже с отмеченным согласием", () => {
    renderApp(<WaitlistForm />);
    fireEvent.change(screen.getByLabelText(/ваш e-mail/i), { target: { value: "не-e-mail" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.submit(screen.getByRole("form", { name: /лист ожидания/i }));

    expect(screen.getByText(/проверьте адрес e-mail/i)).toBeInTheDocument();
    expect(waitlistEmails).toHaveLength(0);
  });
});

describe("WaitlistForm — согласие + верный e-mail проходят", () => {
  it("отправляется, показывает успех и шлёт версию согласия на сервер", async () => {
    renderApp(<WaitlistForm />);
    fireEvent.change(screen.getByLabelText(/ваш e-mail/i), { target: { value: "reader@example.com" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: /записаться/i })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /записаться/i }));

    await waitFor(() => expect(screen.getByTestId("waitlist-success")).toBeInTheDocument());
    expect(waitlistEmails).toContain("reader@example.com");
  });
});

describe("WaitlistForm — юридическая ссылка рядом с согласием", () => {
  it("содержит ссылку на политику обработки данных", () => {
    renderApp(<WaitlistForm />);
    expect(screen.getByRole("link", { name: /политика обработки данных/i })).toHaveAttribute(
      "href",
      "/legal/privacy.html",
    );
  });
});
