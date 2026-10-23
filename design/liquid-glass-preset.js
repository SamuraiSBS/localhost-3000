export function readLiquidGlassPreset(element = document.documentElement) {
  const css = getComputedStyle(element);
  const text = name => css.getPropertyValue(`--liquid-glass-${name}`).trim();
  const number = name => {
    const value = Number(text(name));
    if (!Number.isFinite(value)) throw new Error(`Invalid Liquid Glass token: ${name}`);
    return value;
  };
  const shape = text("shape");
  if (!["circle", "pill"].includes(shape)) throw new Error("Invalid Liquid Glass shape");
  return Object.freeze({ shape, scale: number("scale"), aberration: number("aberration"),
    bevelDepth: number("bevel-depth"), frost: number("frost"), tint: number("tint") });
}
