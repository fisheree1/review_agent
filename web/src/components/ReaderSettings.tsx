import { Icon } from "./Icon";

export type ReadingWidth = "narrow" | "comfortable" | "wide";

interface ReaderSettingsProps {
  fontSize: number;
  readingWidth: ReadingWidth;
  onFontSizeChange: (value: number) => void;
  onReadingWidthChange: (value: ReadingWidth) => void;
}

export function ReaderSettings({
  fontSize,
  readingWidth,
  onFontSizeChange,
  onReadingWidthChange,
}: ReaderSettingsProps) {
  return (
    <div aria-label="阅读设置" className="reader-settings" role="group">
      <button
        aria-label="缩小正文字号"
        className="reader-settings__button"
        disabled={fontSize <= 15}
        onClick={() => onFontSizeChange(Math.max(15, fontSize - 1))}
        type="button"
      >
        <span aria-hidden="true">A−</span>
      </button>
      <span aria-live="polite" className="reader-settings__value">{fontSize}px</span>
      <button
        aria-label="放大正文字号"
        className="reader-settings__button"
        disabled={fontSize >= 21}
        onClick={() => onFontSizeChange(Math.min(21, fontSize + 1))}
        type="button"
      >
        <span aria-hidden="true">A+</span>
      </button>
      <label className="visually-hidden" htmlFor="reading-width">正文行宽</label>
      <select
        id="reading-width"
        onChange={(event) => onReadingWidthChange(event.target.value as ReadingWidth)}
        value={readingWidth}
      >
        <option value="narrow">窄行宽</option>
        <option value="comfortable">舒适行宽</option>
        <option value="wide">宽行宽</option>
      </select>
      <Icon name="book" />
    </div>
  );
}
