type ToggleSwitchProps = {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
};

export function ToggleSwitch({ label, checked, onCheckedChange }: ToggleSwitchProps) {
  return <button className="settings-switch" type="button" role="switch" aria-label={label} aria-checked={checked} onClick={() => onCheckedChange(!checked)}>
    <span className="settings-switch-thumb" aria-hidden="true" />
  </button>;
}
