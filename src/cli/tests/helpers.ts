import { AcpClient, readToken } from "../src/acp/client.js";

export const KERNEL_URL =
  process.env.KERNEL_URL ??
  `ws://localhost:${process.env.KERNEL_PORT ?? "8200"}`;

export function getToken(): string {
  return readToken();
}

export async function connect(): Promise<AcpClient> {
  return AcpClient.connect(KERNEL_URL, getToken());
}

export function assert(condition: boolean, msg: string): void {
  if (!condition) {
    console.error(`FAIL: ${msg}`);
    process.exit(1);
  }
}
