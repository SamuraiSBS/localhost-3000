// Product adapter of the approved ReUI/21st Chart21st CustomBar.
// Source snapshot stays untouched. Changes: real API buckets, touch/keyboard, reduced motion.
import { useState, useRef, useEffect } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { Bar, BarChart, XAxis, ResponsiveContainer } from 'recharts';

function Shape(props: any) {
  const { x, y, width, height, index, value, fill, active, setActive, reduce } = props;
  const expanded = active === index;
  return <g onMouseEnter={() => setActive(index)}>
    <motion.rect y={y} height={height} rx="3" fill={fill}
      initial={false} animate={{ width: expanded ? width : 6, x: expanded ? x : x + (width - 6) / 2 }}
      transition={reduce ? { duration: 0 } : { duration: expanded ? 0.5 : 1, type: 'spring' }}/>
    {expanded && <motion.text initial={reduce ? false : { opacity: 0, y: -10, filter: 'blur(3px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }} transition={{ duration: reduce ? 0 : 0.1 }} x={x + width / 2} y={y - 8} textAnchor="middle" fill={fill}>{value}</motion.text>}
  </g>;
}

export default function RiskChart({ data }: { data: any }) {
  const [active, setActive] = useState<number | undefined>();
  const reduce = useReducedMotion();
  const buckets = data.buckets;
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => { host.current?.closest('.chart-panel')?.classList.add('product-chart-enhanced'); }, []);
  return <div className="product-chart" ref={host}><p className="chart-current" aria-live="polite">{active === undefined ? 'Выберите интервал, чтобы увидеть значения' : `${buckets[active].label}: высокий риск — ${buckets[active].high_risk_services}; условные непродления — ${buckets[active].scenario_nonrenewals}`}</p>
    <div className="chart-canvas" aria-hidden="true"><ResponsiveContainer width="100%" height={260}><BarChart data={buckets} margin={{ top: 30, right: 12, bottom: 12, left: 12 }} onMouseLeave={() => setActive(undefined)}>
      <XAxis dataKey="label" tickFormatter={label => label.startsWith('Просрочено') ? 'Ранее' : label.replace(' дней', '')} tickLine={false} axisLine={false} tick={{ fill: 'var(--lab-muted)', fontSize: 16 }} tickMargin={12}/>
      <Bar dataKey="high_risk_services" fill="var(--lab-accent)" shape={<Shape active={active} setActive={setActive} reduce={reduce}/>} isAnimationActive={!reduce}/>
      <Bar dataKey="scenario_nonrenewals" fill="var(--lab-signal)" shape={<Shape active={active} setActive={setActive} reduce={reduce}/>} isAnimationActive={!reduce}/>
    </BarChart></ResponsiveContainer></div>
    <div className="chart-periods" aria-label="Значения интервалов">{buckets.map((bucket: any, index: number) => <button key={bucket.label} type="button" aria-pressed={active === index} onFocus={() => setActive(index)} onClick={() => setActive(index)} onKeyDown={e => { if (e.key === 'Escape') setActive(undefined); }}><span>{bucket.label}</span><strong>{bucket.high_risk_services} / {bucket.scenario_nonrenewals}</strong></button>)}</div>
    <p className="chart-key"><span>Высокий риск</span><span>Условные непродления</span></p>
  </div>;
}
