// ClippyMe redesign — TopTextControls: the top-edge title-banner config drawer
// shared by BOTH editing surfaces (the Create recipe and the EditClipModal),
// following the hookStyle.jsx / BannerControls precedent: fully controlled
// `value` + `onChange(partial)`, no defaults applied here — the caller's
// adapter/seed resolves those.
//
// Keys mirror the backend top_text_params vocabulary ({ text, size,
// text_color, bg_enabled, bg_color, bg_opacity }).
import { Segmented, Switch } from './primitives';
import { TOP_TEXT_SIZES, TOP_TEXT_BG_COLORS, SUB_COLORS } from './data';

function hexA(hex, a) {
  const h = (hex || '#000').replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

// Live WYSIWYG preview: a dark swatch with a simulated top strip.
export function TopTextPreview({ text, value: v }) {
  const s = v || {};
  const bg = s.bg_enabled ? hexA(s.bg_color || '#000000', s.bg_opacity ?? 0.55) : 'transparent';
  const stroke = s.text_color && s.text_color.toUpperCase() === '#FFFFFF'
    ? '0 2px 6px rgba(0,0,0,.6)' : 'none';
  return (
    <div style={{ padding: 0, background: 'linear-gradient(180deg,#2a2a33,#0c0c11)', borderRadius: 'var(--r-sm)', overflow: 'hidden' }}>
      <div style={{
        padding: '10px 8px', background: bg, textAlign: 'center', fontSize: s.size === 'L' ? 17 : s.size === 'S' ? 13 : 15,
        fontWeight: 700, color: s.text_color || '#fff', lineHeight: 1.2, textShadow: stroke, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {text || 'Title text'}
      </div>
      <div style={{ height: 34 }} />
    </div>
  );
}

function Swatches({ value, onPick, label, colors }) {
  return (
    <div className="cf-row">
      <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>{label}</span>
      <div className="swatches">
        {colors.map((c) => (
          <button key={c} type="button" aria-label={`${label} ${c}`}
            className={'swatch' + ((value || '').toUpperCase() === c.toUpperCase() ? ' on' : '')}
            style={{ background: c }} onClick={() => onPick(c)} />
        ))}
      </div>
    </div>
  );
}

export function TopTextControls({ value: v, onChange }) {
  const s = v || {};
  return (
    <>
      <div className="cf-row">
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>Text</span>
        <textarea className="ta" rows="2" value={s.text || ''} placeholder="e.g. St. Johns BOCC Public comments — SB 686"
          aria-label="Top banner text"
          onChange={(e) => onChange({ text: e.target.value })}></textarea>
      </div>
      <div className="cf-row">
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>Font size</span>
        <Segmented full value={s.size || 'M'} onChange={(id) => onChange({ size: id })} options={TOP_TEXT_SIZES} />
      </div>
      <Swatches label="Text color" value={s.text_color} onPick={(c) => onChange({ text_color: c })} colors={SUB_COLORS} />
      <div className="edit-opt" style={{ padding: '10px 0', border: 0 }}>
        <div className="eo-txt"><div className="eo-t" style={{ fontSize: 13 }}>Background strip</div><div className="eo-d">Translucent band behind the title text</div></div>
        <Switch on={s.bg_enabled !== false} onChange={(on) => onChange({ bg_enabled: on })} />
      </div>
      {s.bg_enabled !== false && (
        <>
          <Swatches label="Strip color" value={s.bg_color} onPick={(c) => onChange({ bg_color: c })} colors={TOP_TEXT_BG_COLORS} />
          <div className="cf-row">
            <span className="field-label" style={{ marginBottom: 9, display: 'flex', justifyContent: 'space-between' }}>
              <span>Strip opacity</span><span className="eo-d">{Math.round((s.bg_opacity ?? 0.55) * 100)}%</span>
            </span>
            <input type="range" min="10" max="100" step="1" value={Math.round((s.bg_opacity ?? 0.55) * 100)}
              onChange={(e) => onChange({ bg_opacity: Number(e.target.value) / 100 })} style={{ width: '100%' }} />
          </div>
        </>
      )}
    </>
  );
}
