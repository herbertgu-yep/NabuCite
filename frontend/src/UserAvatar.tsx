export function UserAvatar({ name }: { name: string }) {
  return <span className="account-avatar" aria-label={name}>{Array.from(name)[0]}</span>
}
