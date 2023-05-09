from dataclasses import dataclass
from typing import Optional, Union, Any

import requests


def submit_get(url: str,
               context: Optional[str] = None) -> Union[dict[str, Any], list[dict[str, Any]], list[str]]:
    extra_context = "" if context is None else f" {context}"
    print(f"submitting GET {url}{extra_context}")
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def submit_put(url: str,
               json: Optional[Union[dict[str, Any], list[dict[str, Any]]]],
               context: Optional[str] = None) -> None:
    extra_context = "" if context is None else f" {context}"
    print(f"submitting PUT {url}{extra_context}")
    response = requests.put(url, json=json)
    response.raise_for_status()


def submit_delete(url: str,
                  context: Optional[str] = None) -> None:
    extra_context = "" if context is None else f" {context}"
    print(f"submitting DELETE {url}{extra_context}")
    response = requests.delete(url)
    response.raise_for_status()


@dataclass
class RenderRequest:
    host: str
    owner: str
    project: str

    def project_url(self) -> str:
        # noinspection HttpUrlsUsage
        return f"http://{self.host}/render-ws/v1/owner/{self.owner}/project/{self.project}"

    def stack_url(self,
                  stack: str) -> str:
        # noinspection HttpUrlsUsage
        return f"{self.project_url()}/stack/{stack}"

    def get_stack_ids(self) -> list[dict[str, Any]]:
        return submit_get(f'{self.project_url()}/stackIds')

    def get_stack_metadata(self,
                           stack: str) -> dict[str, Any]:
        return submit_get(f'{self.stack_url(stack)}')

    def get_tile_bounds_for_z(self,
                              stack: str,
                              z: [float, str]) -> list[dict[str, Any]]:
        return submit_get(f'{self.stack_url(stack)}/z/{z}/tileBounds')

    def get_tile_ids_with_pattern(self,
                                  stack: str,
                                  match_pattern: str) -> list[str]:
        return submit_get(f'{self.stack_url(stack)}/tileIds?matchPattern={match_pattern}')

    def get_tile_spec(self,
                      stack: str,
                      tile_id: str) -> dict[str, Any]:
        return submit_get(f'{self.stack_url(stack)}/tile/{tile_id}')

    def get_resolved_tiles_for_z(self,
                                 stack: str,
                                 z: [float, str]) -> dict[str, Any]:
        return submit_get(f'{self.stack_url(stack)}/z/{z}/resolvedTiles')

    def get_resolved_restart_tiles(self,
                                   stack: str) -> dict[str, Any]:
        return submit_get(f'{self.stack_url(stack)}/resolvedTiles?groupId=restart')

    def set_stack_state(self,
                        stack: str,
                        state: str):
        url = f'{self.stack_url(stack)}/state/{state}'
        submit_put(url=url, json=None, context=None)

    def set_stack_state_to_loading(self,
                                   stack: str):
        self.set_stack_state(stack, 'LOADING')

    def set_stack_state_to_complete(self,
                                    stack: str):
        self.set_stack_state(stack, 'COMPLETE')

    def save_resolved_tiles(self,
                            stack: str,
                            resolved_tiles: dict[str, Any]):
        url = f'{self.stack_url(stack)}/resolvedTiles'
        submit_put(url=url,
                   json=resolved_tiles,
                   context=f'for {len(resolved_tiles["tileIdToSpecMap"])} tile specs')


@dataclass
class MatchRequest:
    host: str
    owner: str
    collection: str

    def collection_url(self) -> str:
        # noinspection HttpUrlsUsage
        return f"http://{self.host}/render-ws/v1/owner/{self.owner}/matchCollection/{self.collection}"

    def get_p_group_ids(self) -> list[str]:
        url = f"{self.collection_url()}/pGroupIds"
        p_group_ids = submit_get(url)
        print(f"retrieved {len(p_group_ids)} pGroupId values for the {self.collection} collection")

        return p_group_ids

    # [
    #   {
    #     "pGroupId": "1.0",
    #     "pId": "23-01-24_000020_0-0-0.1.0",
    #     "qGroupId": "1.0",
    #     "qId": "23-01-24_000020_0-0-1.1.0",
    #     "matchCount": 36
    #   }, ...
    # ]
    def get_pairs_with_match_counts_for_group(self,
                                              group_id: str) -> list[dict[str, Any]]:
        url = f"{self.collection_url()}/pGroup/{group_id}/matchCounts"
        match_counts = submit_get(url)
        print(f"retrieved {len(match_counts)} {self.collection} pairs for groupId {group_id}")
        return match_counts

    def get_match_pairs_for_group(self,
                                  group_id: str,
                                  exclude_match_details: bool = False) -> list[dict[str, Any]]:
        query = "?excludeMatchDetails=true" if exclude_match_details else ""
        url = f"{self.collection_url()}/pGroup/{group_id}/matches{query}"
        match_pairs = submit_get(url)
        print(f"retrieved {len(match_pairs)} {self.collection} pairs for groupId {group_id}")

        return match_pairs

    def get_match_pairs_within_group(self,
                                     group_id: str,
                                     exclude_match_details: bool = False) -> list[dict[str, Any]]:
        query = "?excludeMatchDetails=true" if exclude_match_details else ""
        url = f"{self.collection_url()}/group/{group_id}/matchesWithinGroup{query}"
        match_pairs = submit_get(url)
        print(f"retrieved {len(match_pairs)} {self.collection} pairs for groupId {group_id}")

        return match_pairs

    def save_match_pairs(self,
                         group_id: str,
                         match_pairs: list[dict[str, Any]]):
        if len(match_pairs) > 0:
            url = f"{self.collection_url()}/matches"
            submit_put(url=url,
                       json=match_pairs,
                       context=f"for {len(match_pairs)} pairs with groupId {group_id}")

    def delete_match_pair(self,
                          p_group_id: str,
                          p_id: str,
                          q_group_id: str,
                          q_id: str):
        submit_delete(f"{self.collection_url()}/group/{p_group_id}/id/{p_id}/matchesWith/{q_group_id}/id/{q_id}")
