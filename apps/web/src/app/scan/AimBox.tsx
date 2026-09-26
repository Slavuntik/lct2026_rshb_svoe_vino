import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/**
 * Прицел: пользователь сам обводит нужную бутылку перед поиском (contracts/image-scan.md v0.4.10).
 *
 * Зачем экран вообще есть: выбор нужной бутылки в кадре — главный резерв точности. При идеальном
 * выборе среди рамок детектора top-1 достигает 0,775, при автоматическом — 0,663, и вытащить
 * признак «та самая бутылка» из самого кадра не удалось ни одним из проверенных способов. Рамка
 * снимает неопределённость целиком: у полки рядом стоят вина одной серии, и палец пользователя
 * знает ответ лучше любого детектора.
 *
 * Координаты наружу отдаются в долях кадра (0…1), а не в пикселях: фотография показана вписанной
 * (`object-fit: contain`), поля по краям к кадру не относятся, а итоговое разрешение файла экрану
 * неизвестно. Поэтому положение ручек пересчитывается от прямоугольника самой фотографии внутри
 * контейнера, а не от контейнера.
 */

export type Frame = { x1: number; y1: number; x2: number; y2: number };
type Grip = "move" | "nw" | "ne" | "sw" | "se";

export const DEFAULT_FRAME: Frame = { x1: 0.25, y1: 0.1, x2: 0.75, y2: 0.9 };
const MIN_SIDE = 0.05;

const clamp = (value: number) => Math.min(Math.max(value, 0), 1);

interface AimBoxProps {
  previewUrl: string;
  frame: Frame;
  onFrameChange: (frame: Frame) => void;
  label: string;
}

export function AimBox({ previewUrl, frame, onFrameChange, label }: AimBoxProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<{ grip: Grip; startX: number; startY: number; frame: Frame } | null>(null);
  const [rect, setRect] = useState({ left: 0, top: 0, width: 0, height: 0 });

  /** Прямоугольник самой фотографии внутри контейнера: она вписана целиком, поля не в счёт. */
  const measure = useCallback(() => {
    const element = imageRef.current;
    if (!element || !element.naturalWidth) return;
    const scale = Math.min(
      element.clientWidth / element.naturalWidth,
      element.clientHeight / element.naturalHeight,
    );
    const width = element.naturalWidth * scale;
    const height = element.naturalHeight * scale;
    setRect({ left: (element.clientWidth - width) / 2, top: (element.clientHeight - height) / 2, width, height });
  }, []);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, previewUrl]);

  function startDrag(grip: Grip, event: ReactPointerEvent<HTMLElement>) {
    event.preventDefault();
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    dragRef.current = { grip, startX: event.clientX, startY: event.clientY, frame };
  }

  function onDrag(event: ReactPointerEvent<HTMLElement>) {
    const dragging = dragRef.current;
    if (!dragging || !rect.width || !rect.height) return;
    const dx = (event.clientX - dragging.startX) / rect.width;
    const dy = (event.clientY - dragging.startY) / rect.height;
    const start = dragging.frame;
    const next: Frame = { ...start };

    if (dragging.grip === "move") {
      const width = start.x2 - start.x1;
      const height = start.y2 - start.y1;
      next.x1 = clamp(Math.min(start.x1 + dx, 1 - width));
      next.y1 = clamp(Math.min(start.y1 + dy, 1 - height));
      next.x2 = next.x1 + width;
      next.y2 = next.y1 + height;
    } else {
      if (dragging.grip.includes("w")) next.x1 = clamp(Math.min(start.x1 + dx, start.x2 - MIN_SIDE));
      if (dragging.grip.includes("e")) next.x2 = clamp(Math.max(start.x2 + dx, start.x1 + MIN_SIDE));
      if (dragging.grip.startsWith("n")) next.y1 = clamp(Math.min(start.y1 + dy, start.y2 - MIN_SIDE));
      if (dragging.grip.startsWith("s")) next.y2 = clamp(Math.max(start.y2 + dy, start.y1 + MIN_SIDE));
    }
    onFrameChange(next);
  }

  function endDrag() {
    dragRef.current = null;
  }

  const frameStyle = {
    left: `${rect.left + frame.x1 * rect.width}px`,
    top: `${rect.top + frame.y1 * rect.height}px`,
    width: `${(frame.x2 - frame.x1) * rect.width}px`,
    height: `${(frame.y2 - frame.y1) * rect.height}px`,
  };

  return (
    <div className="aim" onPointerMove={onDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
      <img ref={imageRef} src={previewUrl} alt="" className="aim__photo" onLoad={measure} />
      <div
        className="aim__frame"
        style={frameStyle}
        role="group"
        aria-label={label}
        data-testid="aim-frame"
        onPointerDown={(event) => startDrag("move", event)}
      >
        {(["nw", "ne", "sw", "se"] as const).map((grip) => (
          <span
            key={grip}
            className={`aim__grip aim__grip--${grip}`}
            data-testid={`aim-grip-${grip}`}
            onPointerDown={(event) => {
              event.stopPropagation();
              startDrag(grip, event);
            }}
          />
        ))}
      </div>
    </div>
  );
}
