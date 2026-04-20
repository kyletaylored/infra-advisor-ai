import React, { FormEvent, useEffect, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Flex,
  HStack,
  IconButton,
  Input,
  Spinner,
  Table,
  Text,
  VStack,
} from "@chakra-ui/react";
import { User, createUser, deleteUser, listUsers, patchUser } from "../lib/auth";
import { useAuth } from "../hooks/useAuth";

// ── Trash icon ────────────────────────────────────────────────────────────────

function TrashIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 4h12M5 4V2h6v2M6 7v5M10 7v5M3 4l1 9a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-9" />
    </svg>
  );
}

// ── Checkbox helper ───────────────────────────────────────────────────────────
// Using a plain HTML checkbox styled to avoid Chakra v3 Checkbox compound component complexity.

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
      <Text fontSize="sm" color="gray.700" userSelect="none">
        {label}
      </Text>
    </Box>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function AdminTab() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  // Add user form state
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [newIsService, setNewIsService] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  // Per-row action state
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [patchingId, setPatchingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function loadUsers() {
    setLoadingUsers(true);
    setListError(null);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoadingUsers(false);
    }
  }

  useEffect(() => {
    loadUsers();
  }, []);

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

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await deleteUser(id);
      setUsers((prev) => prev.filter((u) => u.id !== id));
    } catch {
      // ignore — user stays in list
    } finally {
      setDeletingId(null);
    }
  }

  async function handleToggleAdmin(u: User) {
    setPatchingId(u.id);
    try {
      const updated = await patchUser(u.id, { is_admin: !u.is_admin });
      setUsers((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
    } catch {
      // ignore
    } finally {
      setPatchingId(null);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Box flex={1} overflowY="auto" px={6} py={6}>
      <VStack gap={6} align="stretch" maxW="4xl" mx="auto">
        {/* Header */}
        <Box>
          <Text fontSize="lg" fontWeight="semibold" color="gray.800">
            User Management
          </Text>
          <Text fontSize="sm" color="gray.400" mt={0.5}>
            Manage user accounts and permissions
          </Text>
        </Box>

        {/* Add user form */}
        <Box
          bg="white"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="xl"
          p={5}
          boxShadow="xs"
        >
          <Text fontSize="sm" fontWeight="semibold" color="gray.700" mb={4}>
            Add user
          </Text>
          <form onSubmit={handleAddUser}>
            <VStack gap={3} align="stretch">
              <HStack gap={3} align="flex-end">
                <Box flex={1}>
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                    Email
                  </Text>
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
                    _focus={{
                      borderColor: "blue.500",
                      boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)",
                    }}
                  />
                </Box>
                <Box flex={1}>
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                    Password
                  </Text>
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
                    _focus={{
                      borderColor: "blue.500",
                      boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)",
                    }}
                  />
                </Box>
              </HStack>

              <HStack gap={6}>
                <NativeCheckbox
                  id="new-is-admin"
                  label="Admin"
                  checked={newIsAdmin}
                  onChange={setNewIsAdmin}
                  disabled={adding}
                />
                <NativeCheckbox
                  id="new-is-service"
                  label="Service account"
                  checked={newIsService}
                  onChange={setNewIsService}
                  disabled={adding}
                />
                <Text fontSize="xs" color="gray.400">
                  {newIsService
                    ? "Service accounts may use any email domain."
                    : "Regular accounts require @datadoghq.com."}
                </Text>
              </HStack>

              {addError && (
                <Text fontSize="sm" color="red.500">
                  {addError}
                </Text>
              )}

              <Box>
                <Button
                  type="submit"
                  colorPalette="blue"
                  size="sm"
                  borderRadius="lg"
                  disabled={adding}
                >
                  {adding ? <Spinner size="xs" /> : "Add user"}
                </Button>
              </Box>
            </VStack>
          </form>
        </Box>

        {/* Users table */}
        <Box
          bg="white"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="xl"
          boxShadow="xs"
          overflow="hidden"
        >
          {loadingUsers ? (
            <Flex justify="center" align="center" py={10}>
              <Spinner size="sm" color="blue.500" />
            </Flex>
          ) : listError ? (
            <Flex justify="center" align="center" py={10}>
              <Text fontSize="sm" color="red.500">
                {listError}
              </Text>
            </Flex>
          ) : (
            <Table.Root size="sm">
              <Table.Header>
                <Table.Row bg="gray.50">
                  <Table.ColumnHeader
                    fontSize="xs"
                    fontWeight="semibold"
                    color="gray.500"
                    textTransform="uppercase"
                    letterSpacing="wider"
                    py={3}
                    px={4}
                  >
                    Email
                  </Table.ColumnHeader>
                  <Table.ColumnHeader
                    fontSize="xs"
                    fontWeight="semibold"
                    color="gray.500"
                    textTransform="uppercase"
                    letterSpacing="wider"
                    py={3}
                    px={4}
                  >
                    Roles
                  </Table.ColumnHeader>
                  <Table.ColumnHeader
                    fontSize="xs"
                    fontWeight="semibold"
                    color="gray.500"
                    textTransform="uppercase"
                    letterSpacing="wider"
                    py={3}
                    px={4}
                  >
                    Created
                  </Table.ColumnHeader>
                  <Table.ColumnHeader py={3} px={4} w="48px" />
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
                    const isCurrentUser = u.id === currentUser?.id;
                    const isPatching = patchingId === u.id;
                    const isDeleting = deletingId === u.id;

                    return (
                      <Table.Row
                        key={u.id}
                        _hover={{ bg: "gray.50" }}
                        cursor={isPatching ? "wait" : "pointer"}
                        onClick={() => !isPatching && handleToggleAdmin(u)}
                        title="Click to toggle admin role"
                      >
                        <Table.Cell px={4} py={3}>
                          <HStack gap={2}>
                            <Text fontSize="sm" color="gray.800">
                              {u.email}
                            </Text>
                            {isCurrentUser && (
                              <Badge
                                colorPalette="blue"
                                variant="subtle"
                                fontSize="2xs"
                                borderRadius="full"
                                px={1.5}
                              >
                                you
                              </Badge>
                            )}
                          </HStack>
                        </Table.Cell>
                        <Table.Cell px={4} py={3}>
                          <HStack gap={1.5}>
                            {isPatching ? (
                              <Spinner size="xs" color="blue.400" />
                            ) : (
                              <>
                                {u.is_admin && (
                                  <Badge
                                    colorPalette="purple"
                                    variant="subtle"
                                    fontSize="xs"
                                    borderRadius="full"
                                    px={2}
                                  >
                                    Admin
                                  </Badge>
                                )}
                                {u.is_service_account && (
                                  <Badge
                                    colorPalette="orange"
                                    variant="subtle"
                                    fontSize="xs"
                                    borderRadius="full"
                                    px={2}
                                  >
                                    Service
                                  </Badge>
                                )}
                                {!u.is_admin && !u.is_service_account && (
                                  <Text fontSize="xs" color="gray.400">
                                    User
                                  </Text>
                                )}
                              </>
                            )}
                          </HStack>
                        </Table.Cell>
                        <Table.Cell px={4} py={3}>
                          <Text fontSize="xs" color="gray.400">
                            {new Date(u.created_at).toLocaleDateString(undefined, {
                              year: "numeric",
                              month: "short",
                              day: "numeric",
                            })}
                          </Text>
                        </Table.Cell>
                        <Table.Cell
                          px={4}
                          py={3}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <IconButton
                            size="xs"
                            variant="ghost"
                            colorPalette="red"
                            borderRadius="md"
                            aria-label={`Delete ${u.email}`}
                            title={isCurrentUser ? "Cannot delete your own account" : `Delete ${u.email}`}
                            disabled={isCurrentUser || isDeleting || isPatching}
                            onClick={() => handleDelete(u.id)}
                          >
                            {isDeleting ? <Spinner size="xs" /> : <TrashIcon />}
                          </IconButton>
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
          Click any user row to toggle admin role. Deleting your own account is disabled.
        </Text>
      </VStack>
    </Box>
  );
}
