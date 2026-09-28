import { useInfiniteQuery } from "@tanstack/react-query";

import { isProcessing, listDocuments, type DocumentFilters } from "../api/documents";

export function useDocuments(filters?: DocumentFilters) {
  const query = useInfiniteQuery({
    queryKey: filters ? ["documents", filters] : ["documents"],
    queryFn: ({ pageParam }) => listDocuments(pageParam || undefined, filters),
    initialPageParam: "",
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: (currentQuery) => {
      const hasProcessingDocument = currentQuery.state.data?.pages.some((page) =>
        page.items.some((document) => isProcessing(document.status)),
      );
      return hasProcessingDocument ? 1800 : false;
    },
  });

  return {
    documents: query.data?.pages.flatMap((page) => page.items) ?? [],
    error: query.error,
    isLoading: query.isPending,
    isLoadingMore: query.isFetchingNextPage,
    nextCursor: query.hasNextPage ? query.data?.pages.at(-1)?.next_cursor ?? null : null,
    refresh: async () => { await query.refetch(); },
    loadMore: async () => { await query.fetchNextPage(); },
  };
}
