declare module "*.md" {
  const content: string;
  export default content;
}

declare module "diff" {
  export function diffLines(a: string, b: string): Array<{ added?: boolean; removed?: boolean; value: string }>;
  export function createPatch(...args: unknown[]): string;
}
