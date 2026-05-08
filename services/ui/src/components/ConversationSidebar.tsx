import React, { useEffect, useState } from "react";
import {
  Box,
  Flex,
  HStack,
  IconButton,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";
import { Check, MessageSquarePlus, Trash2, X } from "lucide-react";
import { ConversationSummary, deleteConversation, listConversations } from "../lib/api";

interface Props {
  userId: string;
  activeId: string | null;
  onSelect: (conv: ConversationSummary) => void;
  onNew: () => void;
  refreshTrigger?: number;
}

export function ConversationSidebar({ userId, activeId, onSelect, onNew, refreshTrigger }: Props) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    listConversations(userId).then((list) => {
      setConversations(list);
      setLoading(false);
    });
  }, [userId, refreshTrigger]);

  useEffect(() => {
    if (!confirmingId) return;
    const t = setTimeout(() => setConfirmingId(null), 4000);
    return () => clearTimeout(t);
  }, [confirmingId]);

  async function handleDeleteClick(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (confirmingId === id) {
      setConfirmingId(null);
      setDeletingId(id);
      const ok = await deleteConversation(id, userId);
      if (ok) {
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeId === id) onNew();
      }
      setDeletingId(null);
    } else {
      setConfirmingId(id);
    }
  }

  function formatDate(iso: string): string {
    const d = new Date(iso);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 86400000) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (diff < 604800000) return d.toLocaleDateString([], { weekday: "short" });
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  return (
    <Flex
      direction="column"
      w="220px"
      flexShrink={0}
      borderRightWidth="1px"
      borderColor="gray.200"
      bg="white"
      h="full"
      overflow="hidden"
      display={{ base: "none", md: "flex" }}
    >
      {/* Header */}
      <Flex px={3} py={2.5} borderBottomWidth="1px" borderColor="gray.100" align="center" justify="space-between">
        <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
          History
        </Text>
        <IconButton
          aria-label="New conversation"
          size="xs"
          variant="ghost"
          colorPalette="blue"
          borderRadius="md"
          h="22px"
          w="22px"
          minW="22px"
          onClick={onNew}
        >
          <MessageSquarePlus size={13} />
        </IconButton>
      </Flex>

      {/* List */}
      <Box flex={1} overflowY="auto" py={1}>
        {loading ? (
          <Flex justify="center" pt={4}><Spinner size="sm" color="gray.400" /></Flex>
        ) : conversations.length === 0 ? (
          <Text fontSize="xs" color="gray.400" textAlign="center" pt={4} px={3}>
            No conversations yet
          </Text>
        ) : (
          <VStack gap={0} align="stretch">
            {conversations.map((conv) => (
              <Box
                key={conv.id}
                as="button"
                w="full"
                textAlign="left"
                px={3}
                py={2}
                bg={activeId === conv.id ? "blue.50" : "transparent"}
                borderLeftWidth="2px"
                borderLeftColor={activeId === conv.id ? "blue.500" : "transparent"}
                _hover={{ bg: activeId === conv.id ? "blue.50" : "gray.50" }}
                onClick={() => onSelect(conv)}
                role="option"
                aria-selected={activeId === conv.id}
              >
                {confirmingId === conv.id ? (
                  <Flex w="full" align="center" justify="space-between" gap={1}>
                    <Text fontSize="xs" color="red.600" fontWeight="medium" flex={1} truncate>
                      Delete?
                    </Text>
                    <HStack gap={0.5}>
                      <IconButton
                        aria-label="Confirm delete"
                        size="xs"
                        variant="ghost"
                        colorPalette="red"
                        borderRadius="sm"
                        h="18px"
                        w="18px"
                        minW="18px"
                        loading={deletingId === conv.id}
                        onClick={(e) => handleDeleteClick(e, conv.id)}
                      >
                        <Check size={11} />
                      </IconButton>
                      <IconButton
                        aria-label="Cancel delete"
                        size="xs"
                        variant="ghost"
                        colorPalette="gray"
                        borderRadius="sm"
                        h="18px"
                        w="18px"
                        minW="18px"
                        onClick={(e) => { e.stopPropagation(); setConfirmingId(null); }}
                      >
                        <X size={11} />
                      </IconButton>
                    </HStack>
                  </Flex>
                ) : (
                  <Flex justify="space-between" align="flex-start" gap={1}>
                    <VStack gap={0} align="flex-start" flex={1} minW={0}>
                      <Text fontSize="xs" fontWeight={activeId === conv.id ? "semibold" : "normal"} color="gray.700" lineClamp={2}>
                        {conv.title}
                      </Text>
                      <HStack gap={1.5} mt={0.5}>
                        <Text fontSize="10px" color="gray.400" fontFamily="mono">
                          {formatDate(conv.updated_at)}
                        </Text>
                        <Text
                          fontSize="10px"
                          color={conv.backend === "dotnet" ? "teal.500" : "gray.400"}
                          fontFamily="mono"
                        >
                          {conv.backend === "dotnet" ? ".NET" : "PY"}
                        </Text>
                      </HStack>
                    </VStack>
                    <IconButton
                      aria-label="Delete"
                      size="xs"
                      variant="ghost"
                      colorPalette="red"
                      borderRadius="sm"
                      h="18px"
                      w="18px"
                      minW="18px"
                      flexShrink={0}
                      opacity={0.3}
                      _hover={{ opacity: 1 }}
                      onClick={(e) => handleDeleteClick(e, conv.id)}
                      loading={deletingId === conv.id}
                    >
                      <Trash2 size={11} />
                    </IconButton>
                  </Flex>
                )}
              </Box>
            ))}
          </VStack>
        )}
      </Box>
    </Flex>
  );
}
