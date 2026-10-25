# Production integration — 2026-10-25

The same approved sources below now serve real product routes. Original vendor
files remain byte-identical to the registry snapshots. `src/product/RiskChart.tsx`
is a credited product adapter of ReUI CustomBar: actual API buckets replace sample
months/dollars, purple/green encode the two real series, and touch/keyboard value
buttons plus reduced motion are added. The 6px collapsed bar, expansion and value
reveal come from the original. This adapter is not an unchanged demo.
`ProductUpload.tsx` connects the original upload animation to the native single JSON
input and existing confirmation/validation controller. The native input remains
available to keyboard users. Theme/layout adapters use approved tokens and fonts.
The production build includes these notices and the dependency license files.

# Third-party sources in the interaction specimen

All sources were obtained as public components or public npm packages. No paid
component purchase, AI generation, credit purchase or publication was performed.

| Source | Used for | Terms / attribution |
|---|---|---|
| [React Bits / David Haz](https://github.com/DavidHDev/react-bits) | FaultyTerminal, BlurText, AnimatedContent | MIT + Commons Clause; full upstream text in `licenses/react-bits.txt`. Used as part of this website specimen, not offered as a standalone component product. |
| [Magic UI / Dillion Verma](https://magicui.design/docs/components/interactive-hover-button) | InteractiveHoverButton | MIT; full text in `licenses/magicui.txt`. Upstream credits Aayush Bharti. |
| [Aceternity UI / Manu Arora](https://ui.aceternity.com/) | Liquid Glass, File Upload | Public free component registry. Exact snapshots/URLs retained. See official component pages and [licensing page](https://ui.aceternity.com/licence), which is titled for Pro items. A guessed repository LICENSE URL returned 404; no MIT label is inferred from that response. |
| [ReUI / Sean Hello](https://21st.dev/@sean0205/components/c-chart-11) | Animated Bar Chart from 21st snapshot | MIT; full text in `licenses/reui.txt`. `registry/chart-21st.json` preserves the used snapshot; `registry/chart.json` is a separately fetched upstream version. |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | Card and chart primitives returned by 21st resolver | MIT. Their source is retained under `src/components/ui/`. |
| [Geist Project Authors](https://github.com/vercel/geist-font) / Fontsource | Geist Sans / Mono fonts | SIL Open Font License 1.1. Full terms copied from installed packages into `licenses/`. |
| React, React DOM, Motion, Lucide, Tabler Icons, React Dropzone, Recharts, clsx, tailwind-merge | Runtime dependencies | Package license texts copied into `licenses/`; exact versions in `package-lock.json`. |
| [GSAP](https://gsap.com/standard-license/) | Original AnimatedContent dependency | Package identifies its Standard no-charge license; see upstream terms. No root LICENSE file was supplied by this package. |

Original registry files are preserved without edits; see
`npm run check:sources`. Additional adapters/styles are listed in
`../docs/design/REDESIGN_DETAILS.md`. The preview does not claim all original
components are fully accessible or pixel-identical under arbitrary themes,
viewports, font metrics, DPRs or browsers.
