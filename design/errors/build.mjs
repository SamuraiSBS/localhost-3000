import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { renderErrorPage } from "./error-page.mjs";

const outputDirectory = new URL("./", import.meta.url);
const pages = [
  {
    file: "404.html",
    code: "404",
    title: "Страница не найдена",
    message: "Возможно, адрес изменился или ссылка устарела.",
  },
  {
    file: "403.html",
    code: "403",
    title: "Доступ запрещён",
    message: "У вас нет прав для просмотра этой страницы.",
  },
  {
    file: "500.html",
    code: "500",
    title: "Ошибка сервера",
    message: "На сервере произошла ошибка. Попробуйте ещё раз.",
    retry: true,
  },
  {
    file: "connection.html",
    title: "Нет связи с сервером",
    message: "Проверьте подключение к сети, затем попробуйте ещё раз.",
    retry: true,
  },
];

for (const page of pages) {
  const outputPath = new URL(page.file, outputDirectory);
  await writeFile(fileURLToPath(outputPath), renderErrorPage(page), "utf8");
}

process.stdout.write(`Generated ${pages.length} static error pages.\n`);
