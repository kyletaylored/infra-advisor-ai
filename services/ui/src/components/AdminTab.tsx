import React, { FormEvent, useEffect, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Dialog,
  Flex,
  HStack,
  IconButton,
  Input,
  Spinner,
  Table,
  Text,
  VStack,
} from "@chakra-ui/react";
import { Trash2, Shield } from "lucide-react";
import { User, createUser, deleteUser, listUsers, patchUser } from "../lib/auth";
import { useAuth } from "../hooks/useAuth";

// ── Checkbox helper ───────────────────────────────────────────────────────────

interface NativeCheckboxProps {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

function NativeCheckbox({ id, label, checked, onChange, disabled }: NativeCheckboxProps) {
  return (
    <Box as="label" display="flex" alignItems="center" gap={2} cursor={disabled ? "not-allowed" : "pointer"}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ width: 14, height: 14, cursor: disabled ? "not-allowed" : "pointer" }}
      />
      <Text fontSize="sm" color="gray.700" userSelect="none">{label}</Text>
    </Box>
  );
}

// ── Pending action type ───────────────────────────────────────────────────────

type PendingAction =
  | { kind: "grant-admin"; user: User }
  | { kind: "revoke-admin"; user: User }
  | { kind: "delete"; user: User }
  | null;

// ── Main component ────────────────────────────────────────────────────────────

export function AdminTab() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  // Add user form
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [newIsService, setNewIsService] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  // Confirmation dialog
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  async function loadUsers() {
    setLoadingUsers(true);
    setListError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoadingUsers(false);
    }
  }

  useEffect(() => { loadUsers(); }, []);

  // ── Add user ──────────────────────────────────────────────────────────────

  async function handleAddUser(e: FormEvent) {
    e.preventDefault();
    setAddError(null);
    setAdding(true);
    try {
      const created = await createUser({
        email: newEmail,
        password: newPassword,
        is_admin: newIsAdmin,
        is_service_account: newIsService,
      });
      setUsers((prev) => [...prev, created]);
      setNewEmail("");
      setNewPassword("");
      setNewIsAdmin(false);
      setNewIsService(false);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setAdding(false);
    }
  }

  // ── Confirmation dialog ───────────────────────────────────────────────────

  function openConfirm(action: PendingAction) {
    setConfirmError(null);
    setPendingAction(action);
  }

  function closeConfirm() {
    if (confirming) return;
    setPendingAction(null);
    setConfirmError(null);
  }

  async function handleConfirm() {
    if (!pendingAction) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      if (pendingAction.kind === "delete") {
        await deleteUser(pendingAction.user.id);
        setUsers((prev) => prev.filter((u) => u.id !== pendingAction.user.id));
      } else {
        const isAdmin = pendingAction.kind === "grant-admin";
        const updated = await patchUser(pendingAction.user.id, { is_admin: isAdmin });
        setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      }
      setPendingAction(null);
    } catch (err) {
      setConfirmError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setConfirming(false);
    }
  }

  // ── Dialog copy ───────────────────────────────────────────────────────────

  function dialogTitle(): string {
    if (!pendingAction) return "";
    const email = pendingAction.user.email;
    if (pendingAction.kind === "grant-admin") return `Grant admin to ${email}?`;
    if (pendingAction.kind === "revoke-admin") return `Remove admin from ${email}?`;
    return `Delete ${email}?`;
  }

  function dialogBody(): string {
    if (!pendingAction) return "";
    const email = pendingAction.user.email;
    if (pendingAction.kind === "grant-admin")
      return `${email} will be able to manage all users and access all admin features.`;
    if (pendingAction.kind === "revoke-admin")
      return `${email} will lose admin access and be downgraded to a standard user.`;
    return `The account for ${email} will be permanently deleted. This cannot be undone.`;
  }

  function dialogConfirmLabel(): string {
    if (!pendingAction) return "Confirm";
    if (pendingAction.kind === "grant-admin") return "Grant admin";
    if (pendingAction.kind === "revoke-admin") return "Remove admin";
    return "Delete user";
  }

  function dialogConfirmColor(): string {
    if (pendingAction?.kind === "grant-admin") return "blue";
    return "red";
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Box flex={1} overflowY="auto" px={6} py={6}>
      <VStack gap={6} align="stretch" maxW="4xl" mx="auto">

        {/* Header */}
        <Box>
          <Text fontSize="lg" fontWeight="semibold" color="gray.800">User Management</Text>
          <Text fontSize="sm" color="gray.400" mt={0.5}>Manage user accounts and permissions</Text>
        </Box>

        {/* Add user form */}
        <Box bg="white" borderWidth="1px" borderColor="gray.200" borderRadius="xl" p={5} boxShadow="xs">
          <Text fontSize="sm" fontWeight="semibold" color="gray.700" mb={4}>Add user</Text>
          <form onSubmit={handleAddUser}>
            <VStack gap={3} align="stretch">
              <HStack gap={3} align="flex-end">
                <Box flex={1}>
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>Email</Text>
                  <Input
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder={newIsService ? "service@example.com" : "user@datadoghq.com"}
                    required
                    disabled={adding}
                    fontSize="sm"
                    borderRadius="lg"
                    borderColor="gray.300"
                    _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                  />
                </Box>
                <Box flex={1}>
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>Password</Text>
                  <Input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="••••••••"
                    required
                    disabled={adding}
                    fontSize="sm"
                    borderRadius="lg"
                    borderColor="gray.300"
                    _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                  />
                </Box>
              </HStack>
              <HStack gap={6}>
                <NativeCheckbox id="new-is-admin" label="Admin" checked={newIsAdmin}
                  onChange={setNewIsAdmin} disabled={adding} />
                <NativeCheckbox id="new-is-service" label="Service account" checked={newIsService}
                  onChange={setNewIsService} disabled={adding} />
                <Text fontSize="xs" color="gray.400">
                  {newIsService ? "Service accounts may use any email domain." : "Regular accounts require @datadoghq.com."}
                </Text>
              </HStack>
              {addError && <Text fontSize="sm" color="red.500">{addError}</Text>}
              <Box>
                <Button type="submit" colorPalette="blue" size="sm" borderRadius="lg" disabled={adding}>
                  {adding ? <Spinner size="xs" /> : "Add user"}
                </Button>
              </Box>
            </VStack>
          </form>
        </Box>

        {/* Users table */}
        <Box bg="white" borderWidth="1px" borderColor="gray.200" borderRadius="xl" boxShadow="xs" overflow="hidden">
          {loadingUsers ? (
            <Flex justify="center" align="center" py={10}><Spinner size="sm" color="blue.500" /></Flex>
          ) : listError ? (
            <Flex justify="center" align="center" py={10}>
              <Text fontSize="sm" color="red.500">{listError}</Text>
            </Flex>
          ) : (
            <Table.Root size="sm">
              <Table.Header>
                <Table.Row bg="gray.50">
                  {["Email", "Roles", "Created", "Actions"].map((h) => (
                    <Table.ColumnHeader key={h} fontSize="xs" fontWeight="semibold" color="gray.500"
                      textTransform="uppercase" letterSpacing="wider" py={3} px={4}>
                      {h}
                    </Table.ColumnHeader>
                  ))}
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {users.length === 0 ? (
                  <Table.Row>
                    <Table.Cell colSpan={4} textAlign="center" py={8} color="gray.400" fontSize="sm">
                      No users found
                    </Table.Cell>
                  </Table.Row>
                ) : (
                  users.map((u) => {
                    const isSelf = u.id === currentUser?.id;
                    return (
                      <Table.Row key={u.id} _hover={{ bg: "gray.50" }}>

                        {/* Email */}
                        <Table.Cell px={4} py={3}>
                          <HStack gap={2}>
                            <Text fontSize="sm" color="gray.800">{u.email}</Text>
                            {isSelf && (
                              <Badge colorPalette="blue" variant="subtle" fontSize="2xs" borderRadius="full" px={1.5}>
                                you
                              </Badge>
                            )}
                          </HStack>
                        </Table.Cell>

                        {/* Roles */}
                        <Table.Cell px={4} py={3}>
                          <HStack gap={1.5}>
                            {u.is_admin && (
                              <Badge colorPalette="purple" variant="subtle" fontSize="xs" borderRadius="full" px={2}>
                                Admin
                              </Badge>
                            )}
                            {u.is_service_account && (
                              <Badge colorPalette="orange" variant="subtle" fontSize="xs" borderRadius="full" px={2}>
                                Service
                              </Badge>
                            )}
                            {!u.is_admin && !u.is_service_account && (
                              <Text fontSize="xs" color="gray.400">User</Text>
                            )}
                          </HStack>
                        </Table.Cell>

                        {/* Created */}
                        <Table.Cell px={4} py={3}>
                          <Text fontSize="xs" color="gray.400">
                            {new Date(u.created_at).toLocaleDateString(undefined, {
                              year: "numeric", month: "short", day: "numeric",
                            })}
                          </Text>
                        </Table.Cell>

                        {/* Actions */}
                        <Table.Cell px={4} py={3}>
                          <HStack gap={1}>
                            {/* Admin toggle */}
                            <Button
                              size="xs"
                              variant="ghost"
                              colorPalette={u.is_admin ? "orange" : "purple"}
                              borderRadius="md"
                              gap={1}
                              disabled={isSelf}
                              title={
                                isSelf
                                  ? "You cannot change your own admin role"
                                  : u.is_admin
                                  ? "Remove admin role"
                                  : "Grant admin role"
                              }
                              onClick={() =>
                                openConfirm(
                                  u.is_admin
                                    ? { kind: "revoke-admin", user: u }
                                    : { kind: "grant-admin", user: u }
                                )
                              }
                            >
                              <Shield size={13} />
                              {u.is_admin ? "Revoke" : "Grant"}
                            </Button>

                            {/* Delete */}
                            <IconButton
                              size="xs"
                              variant="ghost"
                              colorPalette="red"
                              borderRadius="md"
                              aria-label={`Delete ${u.email}`}
                              title={isSelf ? "You cannot delete your own account" : `Delete ${u.email}`}
                              disabled={isSelf}
                              onClick={() => openConfirm({ kind: "delete", user: u })}
                            >
                              <Trash2 size={13} />
                            </IconButton>
                          </HStack>
                        </Table.Cell>

                      </Table.Row>
                    );
                  })
                )}
              </Table.Body>
            </Table.Root>
          )}
        </Box>

        <Text fontSize="xs" color="gray.400" textAlign="center">
          Use the action buttons to manage roles and accounts. Your own account cannot be modified.
        </Text>
      </VStack>

      {/* Confirmation dialog */}
      <Dialog.Root open={pendingAction !== null} onOpenChange={(e) => !e.open && closeConfirm()}>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content borderRadius="xl" maxW="sm" mx={4}>
            <Dialog.Header px={6} pt={6} pb={2}>
              <Dialog.Title fontSize="md" fontWeight="semibold" color="gray.800">
                {dialogTitle()}
              </Dialog.Title>
            </Dialog.Header>

            <Dialog.Body px={6} py={3}>
              <Text fontSize="sm" color="gray.600">{dialogBody()}</Text>
              {confirmError && (
                <Text fontSize="sm" color="red.500" mt={3}>{confirmError}</Text>
              )}
            </Dialog.Body>

            <Dialog.Footer px={6} pb={6} pt={4} gap={3}>
              <Button
                size="sm"
                variant="ghost"
                colorPalette="gray"
                borderRadius="lg"
                disabled={confirming}
                onClick={closeConfirm}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                colorPalette={dialogConfirmColor()}
                borderRadius="lg"
                disabled={confirming}
                onClick={handleConfirm}
              >
                {confirming ? <Spinner size="xs" /> : dialogConfirmLabel()}
              </Button>
            </Dialog.Footer>
          </Dialog.Content>
        </Dialog.Positioner>
      </Dialog.Root>
    </Box>
  );
}
