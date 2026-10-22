import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { AnswerModels } from "@/api/types";
import { AnswerModelPicker } from "./ModelReadiness";

const available: AnswerModels = {
  errors: [],
  models: [
    { provider: "vllm", model: "qwen36", state: "loaded", default: true },
    {
      provider: "ollama",
      model: "qwen3.5:9b",
      state: "not_loaded",
      default: false,
    },
  ],
};

it("lists the server's and Ollama's models and reports the pick", async () => {
  const onChoose = vi.fn();
  render(
    <AnswerModelPicker
      available={available}
      choice={null}
      onChoose={onChoose}
    />,
  );
  const select = screen.getByLabelText("Answer model");
  expect(
    screen.getByRole("group", { name: "Model server (vLLM)" }),
  ).toBeTruthy();
  expect(screen.getByRole("group", { name: "Ollama (local)" })).toBeTruthy();
  expect(
    screen.getByRole("option", { name: "qwen36 · loaded (default)" }),
  ).toBeTruthy();
  await userEvent.selectOptions(select, "ollama|qwen3.5:9b");
  expect(onChoose).toHaveBeenLastCalledWith(
    expect.objectContaining({ provider: "ollama", model: "qwen3.5:9b" }),
  );
  await userEvent.selectOptions(select, "vllm|qwen36");
  expect(onChoose).toHaveBeenLastCalledWith(null); // the default needs no choice
});
