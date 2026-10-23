/* Export responsive lossless assets. Requires Sharp only at build time. */
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { parseArgs } = require("node:util");
const { values } = parseArgs({ options: { source: { type: "string" }, "sharp-path": { type: "string", default: "sharp" } } });
const sharp = require(values["sharp-path"]);
const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");

(async () => {
  if (!values.source) throw new Error("Pass --source pointing to the generated PNG.");
  const output = path.resolve("design/assets");
  const source = await fs.readFile(values.source);
  const metadata = await sharp(source).metadata();
  await sharp(source).webp({ lossless: true, effort: 6 }).toFile(path.join(output,"columns.webp"));
  const decoded = await sharp(path.join(output,"columns.webp")).raw().toBuffer();
  if (!(await sharp(source).raw().toBuffer()).equals(decoded)) throw new Error("Lossless export changed source pixels.");
  await sharp(source).resize({ width: 768 }).webp({ lossless: true, effort: 6 }).toFile(path.join(output,"columns-768.webp"));
  const artifacts = {};
  for (const name of ["columns.webp","columns-768.webp"]) {
    const data = await fs.readFile(path.join(output,name));
    const size = await sharp(data).metadata();
    artifacts[name] = { bytes: data.length, width: size.width, height: size.height, sha256: hash(data) };
  }
  await fs.writeFile(path.join(output,"columns.provenance.json"), JSON.stringify({
    origin: "OpenAI built-in ImageGen; generated for this project on 2026-10-22; no reference images",
    prompt: "columns.prompt.txt", source: { bytes: source.length, width: metadata.width, height: metadata.height, sha256: hash(source) },
    export: "Sharp 0.35.5; full image is pixel-identical lossless WebP; 768px variant uses Lanczos3 resize and lossless WebP", artifacts,
  }, null, 2)+"\n");
  console.log(JSON.stringify(artifacts));
})().catch(error => { console.error(error.message); process.exitCode=1; });
