import { useEffect, useRef } from 'react';
import { FileUpload } from '../vendor/upload/file-upload';

// Original visual source + native single-file controller/confirmation as the authority.
export default function ProductUpload() {
  const root = useRef<HTMLDivElement>(null);
  const native = document.querySelector<HTMLInputElement>('#snapshot-file')!;
  useEffect(() => {
    const sync = () => { if (root.current) root.current.inert = native.disabled; };
    const observer = new MutationObserver(sync);
    observer.observe(native, { attributes: true, attributeFilter: ['disabled'] }); sync();
    const input = root.current?.querySelector('input');
    if (input) { input.accept = '.json,application/json'; input.multiple = false; input.setAttribute('aria-label', 'Выбрать полный JSON-снимок'); }
    return () => observer.disconnect();
  }, []);
  return <div ref={root} className="source-upload"><FileUpload onChange={files => {
    if (native.disabled || files.length !== 1) return;
    const transfer = new DataTransfer(); transfer.items.add(files[0]); native.files = transfer.files;
    native.dispatchEvent(new Event('change', { bubbles: true }));
  }}/></div>;
}
