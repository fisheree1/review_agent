import { useEffect, useRef } from "react";

interface ShortcutsDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ShortcutsDialog({ isOpen, onClose }: ShortcutsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (isOpen && !dialog.open) dialog.showModal();
    if (!isOpen && dialog.open) dialog.close();
  }, [isOpen]);

  return (
    <dialog className="shortcuts-dialog" onCancel={(event) => { event.preventDefault(); onClose(); }} onClose={onClose} ref={dialogRef}>
      <h2>阅读快捷键</h2>
      <p>当焦点不在输入控件时可使用：</p>
      <dl>
        <div><dt><kbd>[</kbd></dt><dd>上一页</dd></div>
        <div><dt><kbd>]</kbd></dt><dd>下一页</dd></div>
        <div><dt><kbd>R</kbd></dt><dd>展开或收起引用面板</dd></div>
        <div><dt><kbd>?</kbd></dt><dd>打开快捷键说明</dd></div>
        <div><dt><kbd>Esc</kbd></dt><dd>关闭当前面板</dd></div>
      </dl>
      <button className="button button--primary" onClick={onClose} type="button">知道了</button>
    </dialog>
  );
}
