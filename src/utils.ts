import { readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";

export function expandHomePath(value: string): string {
  if (value === "~") {
    return homedir();
  }
  if (value.startsWith("~/") || value.startsWith("~\\")) {
    return homedir() + value.slice(1);
  }
  return value;
}

export function minDate(left: Date, right: Date): Date {
  return left <= right ? left : right;
}

export function maxDate(left: Date, right: Date): Date {
  return left >= right ? left : right;
}

export function compareDesc(left: unknown, right: unknown): number {
  return String(right ?? "").localeCompare(String(left ?? ""));
}

export function sortObject(value: Record<string, any>): Record<string, any> {
  return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)));
}

export function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

export function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

export function safeReaddir(path: string): string[] {
  try {
    return readdirSync(path);
  } catch {
    return [];
  }
}

export function safePathMtime(path: string): number {
  try {
    return statSync(path).mtimeMs;
  } catch {
    return 0;
  }
}

export function requireReadFile(path: string): string {
  return statSync(path).isFile() ? readFileSync(path, "utf8") : "";
}
