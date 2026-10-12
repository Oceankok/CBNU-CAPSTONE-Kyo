import { useState } from 'react';
import styles from './ConfirmButton.module.css';

/**
 * Two-step button with an inline confirmation, instead of window.confirm():
 * some browsers (in-app / embedded) block native dialogs and the click silently does nothing.
 */
export default function ConfirmButton({
  label,
  question,
  confirmLabel = '확인',
  onConfirm,
  className,
  disabled,
  danger = true,
}: {
  label: string;
  question: string;
  confirmLabel?: string;
  onConfirm: () => void;
  className?: string;
  disabled?: boolean;
  danger?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <span className={styles.ask} role="alertdialog" aria-label={question}>
      <span className={styles.question}>{question}</span>
      <button
        type="button"
        className={styles.cancel}
        onClick={() => setAsking(false)}
      >
        취소
      </button>
      <button
        type="button"
        className={danger ? styles.danger : styles.ok}
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
    </span>
  );
}
