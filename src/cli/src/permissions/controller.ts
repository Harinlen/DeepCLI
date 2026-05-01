import { HookEditorComponent } from "@/active-port/coding-agent/modes/components/hook-editor.js";
import { HookInputComponent } from "@/active-port/coding-agent/modes/components/hook-input.js";
import { HookSelectorComponent } from "@/active-port/coding-agent/modes/components/hook-selector.js";
import { matchesKey, type OverlayHandle, type TUI } from "@/tui/index.js";
import type { PermissionRequest, PermissionResult } from "@/acp/client.js";
import { failClosedPermissionResult } from "@/acp/client.js";
import {
  cancelledResult,
  mapPermissionRequest,
  optionBySelectorLabel,
  questionsResult,
  selectedOptionResult,
} from "./mapper.js";
import { PermissionQueue } from "./queue.js";
import type { StructuredQuestion, StructuredQuestionPrompt, ToolPermissionPrompt } from "./types.js";

export type HookPromptHost = {
  showHookSelector(
    title: string,
    options: string[],
    dialogOptions?: {
      outline?: boolean;
      helpText?: string;
      initialIndex?: number;
      timeout?: number;
      onTimeout?: () => void;
      signal?: AbortSignal;
    },
  ): Promise<string | undefined>;
  showHookInput(
    title: string,
    placeholder?: string,
    dialogOptions?: { timeout?: number; onTimeout?: () => void; signal?: AbortSignal },
  ): Promise<string | undefined>;
  showHookEditor?(
    title: string,
    prefill?: string,
    dialogOptions?: { signal?: AbortSignal },
    editorOptions?: { promptStyle?: boolean },
  ): Promise<string | undefined>;
};

export class PermissionController {
  readonly #queue = new PermissionQueue();

  constructor(
    private readonly promptHost: TUI | HookPromptHost,
    private readonly onError: (message: string) => void = () => {},
  ) {}

  handleRequest(req: PermissionRequest): Promise<PermissionResult> {
    return this.#queue.enqueue(async () => {
      try {
        const prompt = mapPermissionRequest(req);
        if (prompt.type === "questions") return await this.#askQuestions(prompt);
        return await this.#askToolPermission(prompt);
      } catch (error) {
        this.onError(`Permission prompt failed: ${(error as Error).message}`);
        return failClosedPermissionResult(req);
      }
    });
  }

  async #askToolPermission(prompt: ToolPermissionPrompt): Promise<PermissionResult> {
    const selected = await this.#showSelector(
      prompt.body,
      prompt.options.map((option) => option.selectorLabel),
    );
    if (!selected) return cancelledResult();
    const option = optionBySelectorLabel(prompt, selected);
    return option ? selectedOptionResult(option.optionId) : cancelledResult();
  }

  async #askQuestions(prompt: StructuredQuestionPrompt): Promise<PermissionResult> {
    const answers: Record<string, string> = {};
    for (const question of prompt.questions) {
      const answer = question.kind === "text"
        ? await this.#askText(question)
        : await this.#askChoice(question);
      if (answer === undefined) return cancelledResult();
      answers[question.question] = answer;
    }
    return questionsResult(prompt, answers);
  }

  async #askChoice(question: StructuredQuestion): Promise<string | undefined> {
    if (!question.multiple) {
      const options = [...question.options, "Other"];
      const selected = await this.#showSelector(this.#questionTitle(question), options);
      if (!selected) return undefined;
      if (selected === "Other") return await this.#askText({ ...question, placeholder: "Type your answer" });
      return selected;
    }

    const picked = new Set<string>();
    while (true) {
      const options = [
        ...question.options.map((option) => `${picked.has(option) ? "[x]" : "[ ]"} ${option}`),
        "Other",
        "Done",
      ];
      const selected = await this.#showSelector(this.#questionTitle(question), options);
      if (!selected) return undefined;
      if (selected === "Done") return Array.from(picked).join(", ");
      if (selected === "Other") {
        const other = await this.#askText({ ...question, placeholder: "Type another answer" });
        if (other === undefined) return undefined;
        if (other.trim()) picked.add(other.trim());
        continue;
      }
      const option = selected.replace(/^\[[ x]\] /, "");
      if (picked.has(option)) picked.delete(option);
      else picked.add(option);
    }
  }

  async #askText(question: StructuredQuestion): Promise<string | undefined> {
    const answer = question.multiline
      ? await this.#showEditor(this.#questionTitle(question), question.placeholder)
      : await this.#showInput(this.#questionTitle(question), question.placeholder);
    if (answer === undefined) return undefined;
    if (question.maxLength !== undefined && answer.length > question.maxLength) {
      return answer.slice(0, question.maxLength);
    }
    return answer;
  }

  #showSelector(title: string, options: string[]): Promise<string | undefined> {
    const host = this.#hookPromptHost();
    if (host) return host.showHookSelector(title, options, { outline: true });

    return new Promise((resolve) => {
      let handle: OverlayHandle | undefined;
      let removeCtrlCListener: (() => void) | undefined;
      let component: HookSelectorComponent;
      const finish = (value: string | undefined) => {
        removeCtrlCListener?.();
        component.dispose();
        handle?.hide();
        resolve(value);
      };
      component = new HookSelectorComponent(
        title,
        options,
        (option) => finish(option),
        () => finish(undefined),
        { tui: this.tui, outline: true },
      );
      removeCtrlCListener = this.#cancelOnCtrlC(() => finish(undefined));
      handle = this.tui.showOverlay(component, { anchor: "bottom-center", width: "90%", maxHeight: "80%" });
    });
  }

  #showInput(title: string, placeholder?: string): Promise<string | undefined> {
    const host = this.#hookPromptHost();
    if (host) return host.showHookInput(title, placeholder);

    return new Promise((resolve) => {
      let handle: OverlayHandle | undefined;
      let removeCtrlCListener: (() => void) | undefined;
      let component: HookInputComponent;
      const finish = (value: string | undefined) => {
        removeCtrlCListener?.();
        component.dispose();
        handle?.hide();
        resolve(value);
      };
      component = new HookInputComponent(
        title,
        placeholder,
        (value) => finish(value),
        () => finish(undefined),
        { tui: this.tui },
      );
      removeCtrlCListener = this.#cancelOnCtrlC(() => finish(undefined));
      handle = this.tui.showOverlay(component, { anchor: "bottom-center", width: "90%", maxHeight: "80%" });
    });
  }

  #showEditor(title: string, prefill?: string): Promise<string | undefined> {
    const host = this.#hookPromptHost();
    if (host?.showHookEditor) return host.showHookEditor(title, prefill, undefined, { promptStyle: true });

    return new Promise((resolve) => {
      let handle: OverlayHandle | undefined;
      let removeCtrlCListener: (() => void) | undefined;
      let component: HookEditorComponent;
      const finish = (value: string | undefined) => {
        removeCtrlCListener?.();
        handle?.hide();
        resolve(value);
      };
      component = new HookEditorComponent(
        this.tui,
        title,
        prefill,
        (value) => finish(value),
        () => finish(undefined),
      );
      removeCtrlCListener = this.#cancelOnCtrlC(() => finish(undefined));
      handle = this.tui.showOverlay(component, { anchor: "bottom-center", width: "90%", maxHeight: "80%" });
    });
  }

  #cancelOnCtrlC(cancel: () => void): () => void {
    return this.tui.addInputListener((data) => {
      if (!matchesKey(data, "ctrl+c")) return undefined;
      cancel();
      return { consume: true };
    });
  }

  #hookPromptHost(): HookPromptHost | undefined {
    if ("showHookSelector" in this.promptHost && "showHookInput" in this.promptHost) {
      return this.promptHost;
    }
    return undefined;
  }

  get tui(): TUI {
    return this.promptHost as TUI;
  }

  #questionTitle(question: StructuredQuestion): string {
    return `**${question.header}**\n\n${question.question}`;
  }
}
