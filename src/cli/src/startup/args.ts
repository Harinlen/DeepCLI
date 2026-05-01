export interface CliArgs {
  kernelUrl?: string;
  port?: number;
  sessionId?: string;
  newSession: boolean;
  theme?: string;
  print: boolean;
  prompt?: string;
  help: boolean;
}

export class ArgError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ArgError";
  }
}

export function parseCliArgs(argv = process.argv.slice(2)): CliArgs {
  const args: CliArgs = {
    newSession: false,
    print: false,
    help: false,
  };
  const promptParts: string[] = [];

  for (let i = 0; i < argv.length; i++) {
    const item = argv[i];
    switch (item) {
      case "--help":
      case "-h":
        args.help = true;
        break;
      case "--kernel":
        args.kernelUrl = requireValue(argv, ++i, "--kernel");
        break;
      case "--port": {
        const port = Number(requireValue(argv, ++i, "--port"));
        if (!Number.isInteger(port) || port <= 0) throw new ArgError("--port must be a positive integer");
        args.port = port;
        break;
      }
      case "--session":
        args.sessionId = requireValue(argv, ++i, "--session");
        break;
      case "--new":
        args.newSession = true;
        break;
      case "--theme":
        args.theme = requireValue(argv, ++i, "--theme");
        break;
      case "--print":
        args.print = true;
        break;
      case "--":
        promptParts.push(...argv.slice(i + 1));
        i = argv.length;
        break;
      default:
        if (item.startsWith("-")) throw new ArgError(`Unknown option: ${item}`);
        promptParts.push(item);
    }
  }

  if (args.sessionId && args.newSession) {
    throw new ArgError("--session and --new are mutually exclusive");
  }
  if (promptParts.length > 0) args.prompt = promptParts.join(" ");
  return args;
}

function requireValue(argv: string[], index: number, flag: string): string {
  const value = argv[index];
  if (!value || value.startsWith("--")) throw new ArgError(`${flag} requires a value`);
  return value;
}

export function usage(): string {
  return [
    "Usage: deepcli [--kernel <ws-url>] [--port <port>] [--session <id> | --new] [--theme <name>] [--print] [prompt]",
    "",
    "Options:",
    "  --kernel <ws-url>   Kernel WebSocket URL",
    "  --port <port>       Kernel port on localhost",
    "  --session <id>      Load an existing session",
    "  --new               Create a new session",
    "  --theme <name>      Override configured theme",
    "  --print             Send prompt and print streamed output without TUI picker",
  ].join("\n");
}
