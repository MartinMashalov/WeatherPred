import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import katex from 'katex';
import { marked } from 'marked';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(here);
const site = path.resolve(process.argv[2] || path.join(root, 'tmp/personal-site'));
const repo = 'https://github.com/MartinMashalov/WeatherPred/blob/main/';
const read = p => fs.readFileSync(p, 'utf8');
function wrap(title, filename, body, script = false) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title} — Martin Mashalov</title><meta name="description" content="WeatherPred: auditable weather-market research, trading strategies, mathematics and realistic paper execution."><meta name="author" content="Martin Mashalov"><meta property="og:title" content="${title}"><meta property="og:type" content="article"><meta property="og:url" content="https://martinmashalov.github.io/${filename}"><meta property="og:image" content="https://martinmashalov.github.io/assets/og.png"><link rel="canonical" href="https://martinmashalov.github.io/${filename}"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900&family=IBM+Plex+Mono:wght@400;500&family=Instrument+Sans:wght@400..700&display=swap" rel="stylesheet"><link rel="stylesheet" href="styles.css"><link rel="stylesheet" href="thesis.css"><link rel="stylesheet" href="weatherpred.css"></head><body><div class="gridlines" aria-hidden="true">${'<i></i>'.repeat(12)}</div><main class="shell"><header class="masthead"><b><a href="index.html" style="text-decoration:none">Martin Mashalov</a></b><nav aria-label="Project navigation"><a href="weatherpred.html">Project</a><a href="weatherpred-math.html">Math</a><a href="index.html#contact">Contact</a></nav></header>${body}</main>${script ? '<script src="weatherpred.js" defer></script>' : ''}</body></html>`;
}
function documentPage(source, title, filename) {
  let text = read(path.join(root, 'docs', source)).replace(/^# .*\n/, '');
  // Literal currency is not a LaTeX delimiter. Paired numeric math followed by ^ or $ is preserved.
  text = text.replace(/(?<!\\)\$(\d+(?:\.\d+)?)(?=\s|[,;:]|\.(?!\d))/g, (_, amount) => '\\$' + amount);
  const formulas = [];
  const saveMath = (tex, displayMode) => {
    const result = katex.renderToString(tex, { output: 'mathml', displayMode, throwOnError: true, trust: false });
    formulas.push(displayMode ? `<span class="math-block">${result}</span>` : result);
    return `MATHSNIPPET${formulas.length - 1}END`;
  };
  text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex) => saveMath(tex, true));
  text = text.replace(/(?<!\\)\$([^$]+?)\$/g, (_, tex) => saveMath(tex, false));
  let html = marked.parse(text);
  html = html.replace(/MATHSNIPPET(\d+)END/g, (_, i) => formulas[Number(i)]);
  html = html.replace(/href="([^"]+)"/g, (all, href) => {
    if (/^(https?:|#)/.test(href)) return all;
    if (href === 'MATHEMATICS.md') return 'href="weatherpred-math.html"';
    if (href === 'STRATEGIES.md') return 'href="weatherpred-strategies.html"';
    if (href === 'INTERVIEW_GUIDE.md') return 'href="weatherpred-interview.html"';
    return `href="${repo + path.posix.normalize('docs/' + href)}"`;
  });
  const headings = [];
  html = html.replace(/<h2>(.*?)<\/h2>/g, (_, heading) => {
    const id = 'section-' + (headings.length + 1);
    headings.push(`<a href="#${id}">${heading}</a>`);
    return `<h2 id="${id}">${heading}</h2>`;
  });
  const hero = `<section class="hero doc-hero"><p class="eyebrow">WeatherPred / Technical guide</p><h1 class="doc-title">${title}</h1><p class="lede">Implemented methods, worked examples and the limits of the evidence.</p><div class="project-links"><a href="weatherpred.html">Project overview</a><a href="weatherpred-strategies.html">Strategies</a><a href="weatherpred-math.html">Mathematics</a><a href="weatherpred-interview.html">Project brief</a><a href="${repo}docs/${source}">Source document ↗</a></div><nav class="contents" aria-label="Guide contents">${headings.join('')}</nav></section>`;
  fs.writeFileSync(path.join(site, filename), wrap(title, filename, hero + `<article class="article-body">${html}</article>`));
  return { page: filename, formulas: formulas.length, sections: headings.length };
}
fs.writeFileSync(path.join(site, 'weatherpred.html'), wrap('WeatherPred: from weather data to paper trades', 'weatherpred.html', read(path.join(here, 'case-study.html')), true));
for (const file of ['weatherpred.css', 'weatherpred.js']) fs.copyFileSync(path.join(here, file), path.join(site, file));
fs.copyFileSync(path.join(root, 'evidence/explorer.html'), path.join(site, 'weatherpred-results.html'));
console.log(JSON.stringify(documentPage('MATHEMATICS.md', 'The math behind WeatherPred', 'weatherpred-math.html')));
console.log(JSON.stringify(documentPage('STRATEGIES.md', 'Every strategy, explained', 'weatherpred-strategies.html')));
console.log(JSON.stringify(documentPage('INTERVIEW_GUIDE.md', 'Project brief and interview guide', 'weatherpred-interview.html')));
