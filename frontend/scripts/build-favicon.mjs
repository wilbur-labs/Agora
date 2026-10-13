// Regenerates src/app/favicon.ico from src/app/icon.svg (CR-55).
// The .ico also serves as the desktop shortcut icon (agora.ps1 `shortcut`),
// so it carries the sizes Windows asks for, each as an embedded PNG.
// Usage: node scripts/build-favicon.mjs
import { readFile, writeFile } from "node:fs/promises";
import sharp from "sharp";

const svg = await readFile(new URL("../src/app/icon.svg", import.meta.url));
const sizes = [16, 24, 32, 48, 64, 128, 256];
const pngs = await Promise.all(
  sizes.map((size) => sharp(svg, { density: Math.ceil((72 * size) / 32) * 2 }).resize(size, size).png().toBuffer()),
);

const header = Buffer.alloc(6);
header.writeUInt16LE(0, 0); // reserved
header.writeUInt16LE(1, 2); // type: icon
header.writeUInt16LE(sizes.length, 4);

const entries = [];
let offset = 6 + 16 * sizes.length;
sizes.forEach((size, i) => {
  const entry = Buffer.alloc(16);
  entry.writeUInt8(size >= 256 ? 0 : size, 0); // 0 means 256
  entry.writeUInt8(size >= 256 ? 0 : size, 1);
  entry.writeUInt8(0, 2); // palette
  entry.writeUInt8(0, 3); // reserved
  entry.writeUInt16LE(1, 4); // colour planes
  entry.writeUInt16LE(32, 6); // bits per pixel
  entry.writeUInt32LE(pngs[i].length, 8);
  entry.writeUInt32LE(offset, 12);
  offset += pngs[i].length;
  entries.push(entry);
});

const out = new URL("../src/app/favicon.ico", import.meta.url);
await writeFile(out, Buffer.concat([header, ...entries, ...pngs]));
console.log(`favicon.ico: ${sizes.join("/")} px`);
