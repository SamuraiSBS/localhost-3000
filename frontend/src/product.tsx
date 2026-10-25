import React, { useEffect, useState, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig, animate, useReducedMotion } from 'motion/react';
import FaultyTerminal from './vendor/faulty/FaultyTerminal';
import BlurText from './vendor/blur/BlurText';
import { LiquidGlass } from './vendor/glass/liquid-glass';
import { InteractiveHoverButton } from './vendor/button/interactive-hover-button';
import './product.css';
const AnimatedContent = React.lazy(() => import('./vendor/animated/AnimatedContent'));
const RiskChart = React.lazy(() => import('./product/RiskChart'));
const ProductUpload = React.lazy(() => import('./product/ProductUpload'));

function Background() {
  const reduced = useReducedMotion();
  const [paused, setPaused] = useState(() => localStorage.getItem('motion-paused') === 'true');
  const [hidden, setHidden] = useState(document.hidden);
  useEffect(() => { const update = () => setHidden(document.hidden); document.addEventListener('visibilitychange', update); return () => document.removeEventListener('visibilitychange', update); }, []);
  return <><div className="signal-background" aria-hidden="true"><FaultyTerminal tint="#a7ef9e" glyphs="blocks" scale={1.5} digitSize={1.2} timeScale={0.5} brightness={0.75} glow={0.5} persistence={0.5} scanlineIntensity={0.5} curvature={0.1} mouseStrength={0.5} mouseGlow={0.6} mouseReact clickEffect="glitch" scramble={0.5} glitchAmount={1} vignette={0.25} variation={0.42} dpr={1} pause={paused || !!reduced || hidden} /></div><button className="motion-toggle" type="button" aria-pressed={paused} onClick={() => { setPaused(!paused); localStorage.setItem('motion-paused', String(!paused)); }}>{paused ? 'Включить фон' : 'Пауза фона'}</button></>;
}

function HeroNote() {
  const note = <LiquidGlass radius={12} className="signal-glass" contentClassName="signal-glass-content"><span className="signal-dot" aria-hidden="true"/><p>У каждого риска<br/>есть причина.</p><a href="/#how-it-works">Посмотреть, как это работает <span aria-hidden="true">↘</span></a></LiquidGlass>;
  return <Suspense fallback={note}><AnimatedContent distance={30} duration={0.8} blur={8} trigger="load" style={{ visibility: 'visible' }}>{note}</AnimatedContent></Suspense>;
}

function mount(node: Element, children: React.ReactNode) { createRoot(node).render(<MotionConfig reducedMotion="user">{children}</MotionConfig>); }
const background = document.getElementById('interface-background');
if (background) mount(background, <Background/>);
const enter = document.getElementById('enter-action');
if (enter) mount(enter, <InteractiveHoverButton className="enter-button" aria-label="Открыть рабочую область" onClick={() => location.assign('/login')}>Открыть рабочую область</InteractiveHoverButton>);
const hero = document.getElementById('hero-note');
if (hero) mount(hero, <HeroNote/>);
const upload = document.getElementById('upload-preview');
if (upload) mount(upload, <Suspense fallback={null}><ProductUpload/></Suspense>);
const headline = document.querySelector('[data-blur-heading]');
if (headline) mount(headline, <BlurText text={headline.textContent || ''} animateBy="words" delay={100} trigger="load" className="heading-reveal"/>);

// Controllers retain all API/revision logic; the chart receives their exact accepted payload.
const chartRoots = new WeakMap<Element, ReturnType<typeof createRoot>>();
function renderChart(host: Element) {
  const data = (host as any).chartData;
  if (!data) return;
  let root = chartRoots.get(host);
  if (!root) { root = createRoot(host); chartRoots.set(host, root); }
  root.render(<MotionConfig reducedMotion="user"><Suspense fallback={<p>Готовим график…</p>}><RiskChart data={data}/></Suspense></MotionConfig>);
}
document.addEventListener('risk-chart-ready', event => renderChart((event as CustomEvent).detail));
document.querySelectorAll('[data-risk-chart]').forEach(renderChart);

// Small enhancements never own business DOM or delay the initial readable content.
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const observer = new IntersectionObserver(entries => entries.forEach(entry => {
  if (!entry.isIntersecting) return;
  if (!reduce.matches) animate(entry.target, { opacity: [0.6, 1], y: [16, 0] }, { duration: 0.45, ease: [0.16, 1, 0.3, 1] });
  observer.unobserve(entry.target);
}), { threshold: 0.08 });
document.querySelectorAll('main > .section, .landing-story, .landing-steps').forEach(node => observer.observe(node));

const table = document.getElementById('customers-table');
if (table) {
  const labelCells = () => {
    const labels = [...table.querySelectorAll('thead th')].map(node => node.textContent?.trim() || '');
    table.querySelectorAll('tbody tr').forEach(row => [...row.children].forEach((cell, index) => cell.setAttribute('data-label', labels[index])));
  };
  new MutationObserver(labelCells).observe(table, { childList: true, subtree: true }); labelCells();
}
addEventListener('pagehide', () => observer.disconnect());
