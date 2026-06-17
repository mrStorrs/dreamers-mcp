#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const candidates = process.platform === "win32"
  ? [
      ["py", ["-3"]],
      ["python", []],
      ["python3", []],
    ]
  : [
      ["python3", []],
      ["python", []],
    ];

const unittestArgs = ["-m", "unittest", "discover", "-s", "tests"];

for (const [command, prefixArgs] of candidates) {
  const result = spawnSync(command, [...prefixArgs, ...unittestArgs], {
    stdio: "inherit",
  });
  if (result.error?.code === "ENOENT") {
    continue;
  }
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.signal) {
    console.error(`compat tests terminated by ${result.signal}`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error("python runtime is required to run compatibility tests");
process.exit(1);
