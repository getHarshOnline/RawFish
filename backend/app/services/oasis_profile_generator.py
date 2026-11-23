"""
OASIS Agent Profile Generator
Converts entities from the Zep graph into the Agent Profile format required by the OASIS simulation platform.

Optimizations:
1. Invokes Zep retrieval to enrich node information with a second pass
2. Improved prompts that generate very detailed personas
3. Distinguishes between individual entities and abstract group entities
"""

import json
import random
import time
from typing import Dict, Any, Callable, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from zep_cloud.client import Zep

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, get_locale, set_locale, t
from .zep_entity_reader import EntityNode

logger = get_logger('rawfish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile data structure"""
    # Common fields
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str

    # Optional fields - Reddit style
    karma: int = 1000

    # Optional fields - Twitter style
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500

    # Additional persona information
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)

    # Source entity information
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Convert to Reddit platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS library requires the field name to be 'username' (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }

        # Add extra persona information (if present)
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Convert to Twitter platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS library requires the field name to be 'username' (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }

        # Add extra persona information
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a complete dictionary format"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS Profile Generator

    Converts entities from the Zep graph into Agent Profiles required for OASIS simulation.

    Features:
    1. Uses Zep graph search to fetch richer context
    2. Generates very detailed personas (basic info, career history, personality traits, social media behavior, etc.)
    3. Distinguishes between individual entities and abstract group entities
    """

    # MBTI type list
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]

    # Common country list
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France",
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]

    # Individual-type entities (require concrete persona generation)
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure",
        "expert", "faculty", "official", "journalist", "activist"
    ]

    # Group/institution-type entities (require representative group persona generation)
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo",
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
    ):
        self.client = LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
        )
        self.api_key = self.client.config.api_key
        self.base_url = self.client.config.base_url
        self.model_name = self.client.config.model

        # Zep client used to retrieve richer context
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id

        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Zep client: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        Generate an OASIS Agent Profile from a Zep entity

        Args:
            entity: Zep entity node
            user_id: User ID (used by OASIS)
            use_llm: Whether to use an LLM to generate a detailed persona

        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"

        # Basic information
        name = entity.name
        user_name = self._generate_username(name)

        # Build context information
        context = self._build_entity_context(entity)

        if use_llm:
            # Use LLM to generate a detailed persona
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # Use rule-based generation for a basic persona
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """Generate a username"""
        # Strip special characters and convert to lowercase
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')

        # Append a random suffix to avoid collisions
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"

    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Use Zep graph hybrid search to retrieve rich entity-related information

        Zep has no built-in hybrid search endpoint, so we must search edges and nodes
        separately and then merge the results. Issuing the requests in parallel
        improves efficiency.

        Args:
            entity: Entity node object

        Returns:
            Dict containing facts, node_summaries, and context
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # A graph_id is required to perform the search
        if not self.graph_id:
            logger.debug(f"Skipping Zep retrieval: graph_id is not set")
            return results

        comprehensive_query = t('progress.zepSearchQuery', name=entity_name)

        # Capture the narrowed non-None Zep client locally so the inner
        # closures (search_edges / search_nodes) can see it as non-Optional.
        # Without this, Pyright cannot prove the L302 ``if not self.zep_client``
        # guard propagates into the closure scope.
        zep_client = self.zep_client
        assert zep_client is not None, "guarded by `if not self.zep_client` above"

        def search_edges():
            """Search edges (facts/relationships) with retry"""
            max_retries = 3
            delay = 2.0

            for attempt in range(max_retries):
                try:
                    return zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep edge search attempt {attempt + 1} failed: {str(e)[:80]}, retrying...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep edge search still failed after {max_retries} attempts: {e}")
            return None

        def search_nodes():
            """Search nodes (entity summaries) with retry"""
            max_retries = 3
            delay = 2.0

            for attempt in range(max_retries):
                try:
                    return zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep node search attempt {attempt + 1} failed: {str(e)[:80]}, retrying...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep node search still failed after {max_retries} attempts: {e}")
            return None

        try:
            # Run edge and node searches in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)

                # Collect results
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)

            # Process edge search results
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)

            # Process node search results
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"Related entity: {node.name}")
            results["node_summaries"] = list(all_summaries)

            # Build combined context
            context_parts = []
            if results["facts"]:
                context_parts.append("Facts:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("Related entities:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)

            logger.info(f"Zep hybrid search complete: {entity_name}, retrieved {len(results['facts'])} facts, {len(results['node_summaries'])} related nodes")

        except concurrent.futures.TimeoutError:
            logger.warning(f"Zep search timeout ({entity_name})")
        except Exception as e:
            logger.warning(f"Zep search failed ({entity_name}): {e}")

        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        Build the full context for an entity

        Includes:
        1. The entity's own edge information (facts)
        2. Detailed information from related nodes
        3. Rich information retrieved via Zep hybrid search
        """
        context_parts = []

        # 1. Add entity attributes
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Entity Attributes\n" + "\n".join(attrs))

        # 2. Add related edge information (facts/relationships)
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # no count limit
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")

                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (related entity)")
                    else:
                        relationships.append(f"- (related entity) --[{edge_name}]--> {entity.name}")

            if relationships:
                context_parts.append("### Related Facts and Relationships\n" + "\n".join(relationships))

        # 3. Add detailed information for related nodes
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # no count limit
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")

                # Filter out default labels
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""

                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")

            if related_info:
                context_parts.append("### Related Entity Information\n" + "\n".join(related_info))

        # 4. Use Zep hybrid search to gather richer information
        zep_results = self._search_zep_for_entity(entity)

        if zep_results.get("facts"):
            # Deduplicate: exclude facts that already exist
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Facts Retrieved via Zep\n" + "\n".join(f"- {f}" for f in new_facts[:15]))

        if zep_results.get("node_summaries"):
            context_parts.append("### Related Nodes Retrieved via Zep\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))

        return "\n\n".join(context_parts)

    def _is_individual_entity(self, entity_type: str) -> bool:
        """Check whether the entity is an individual type"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES

    def _is_group_entity(self, entity_type: str) -> bool:
        """Check whether the entity is a group/institution type"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        Use an LLM to generate a very detailed persona

        Branches based on entity type:
        - Individual entities: generate a concrete character profile
        - Group/institution entities: generate a representative account profile
        """

        is_individual = self._is_individual_entity(entity_type)

        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # Try multiple times until success or the max retry count is reached
        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            try:
                result = self.client.chat_json(
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7 - (attempt * 0.1),
                    max_tokens=8192,
                )

                if "bio" not in result or not result["bio"]:
                    result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                if "persona" not in result or not result["persona"]:
                    result["persona"] = entity_summary or f"{entity_name} is a {entity_type}."
                return result

            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # Exponential backoff

        logger.warning(f"LLM failed to generate persona ({max_attempts} attempts): {last_error}, falling back to rule-based generation")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repair truncated JSON (output cut off by the max_tokens limit)"""
        # If the JSON was truncated, try to close it
        content = content.strip()

        # Count unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')

        # Check for an unclosed string
        # Simple heuristic: if the last char is not a quote, comma, or closing bracket, a string may have been truncated
        if content and content[-1] not in '",}]':
            # Try to close the string
            content += '"'

        # Close brackets
        content += ']' * open_brackets
        content += '}' * open_braces

        return content

    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Attempt to repair broken JSON"""
        import re

        # 1. First try to fix truncation
        content = self._fix_truncated_json(content)

        # 2. Try to extract the JSON portion
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()

            # 3. Handle newline issues inside strings
            # Find every string value and replace any newlines in them
            def fix_string_newlines(match):
                s = match.group(0)
                # Replace actual newlines inside the string with spaces
                s = s.replace('\n', ' ').replace('\r', ' ')
                # Collapse extra whitespace
                s = re.sub(r'\s+', ' ', s)
                return s

            # Match JSON string values
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)

            # 4. Try to parse
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError:
                # 5. If still failing, try a more aggressive repair
                try:
                    # Remove all control characters
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # Collapse all consecutive whitespace
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass

        # 6. Try to extract partial information from the content
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # may be truncated

        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} is a {entity_type}.")

        # If we extracted meaningful content, mark it as fixed
        if bio_match or persona_match:
            logger.info(f"Extracted partial information from broken JSON")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }

        # 7. Total failure: return a baseline structure
        logger.warning(f"JSON repair failed; returning baseline structure")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} is a {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Get the system prompt"""
        base_prompt = "You are an expert at generating social media user profiles. Produce detailed, realistic personas for use in public-opinion simulation, staying as faithful as possible to the existing real-world situation. You MUST return valid JSON, and no string value may contain unescaped newline characters."
        return f"{base_prompt}\n\n{get_language_instruction()}"
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build the detailed persona prompt for an individual entity"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no additional context"

        return f"""Generate a detailed social media user persona for the entity, staying as faithful as possible to the existing real-world situation.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context information:
{context_str}

Please produce JSON containing the following fields:

1. bio: social media bio, ~200 words
2. persona: detailed persona description (around 2000 words of plain text). It must cover:
   - Basic information (age, occupation, educational background, location)
   - Character background (significant experiences, connection to the event, social relationships)
   - Personality traits (MBTI type, core personality, emotional expression style)
   - Social media behavior (posting frequency, content preferences, interaction style, language characteristics)
   - Stances and viewpoints (attitude toward the topic, content that might anger or move them)
   - Distinctive traits (catchphrases, unusual experiences, personal hobbies)
   - Personal memory (an important part of the persona: describe this individual's connection to the event and the actions and reactions they have already taken in it)
3. age: age as a number (must be an integer)
4. gender: gender, must be English: "male" or "female"
5. mbti: MBTI type (such as INTJ, ENFP, etc.)
6. country: country (in English, such as "China")
7. profession: occupation
8. interested_topics: an array of topics of interest

Important:
- Every field value must be a string or number; do not use newline characters
- persona must be a single coherent text description
- {get_language_instruction()} (the gender field MUST use the English values male/female)
- Content must be consistent with the entity information
- age must be a valid integer; gender must be "male" or "female"
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build the detailed persona prompt for a group/institution entity"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no additional context"

        return f"""Generate a detailed social media account profile for the institution/group entity, staying as faithful as possible to the existing real-world situation.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context information:
{context_str}

Please produce JSON containing the following fields:

1. bio: official-account bio, ~200 words, professional and dignified
2. persona: detailed account profile description (around 2000 words of plain text). It must cover:
   - Basic information about the institution (formal name, nature of the institution, founding background, primary functions)
   - Account positioning (account type, target audience, core functions)
   - Voice style (language characteristics, common phrasing, topics that are off-limits)
   - Posting characteristics (content types, posting frequency, active time windows)
   - Stance and attitude (official position on core topics, how controversies are handled)
   - Special notes (the group portrait it represents, operational habits)
   - Institutional memory (an important part of the institution's persona: describe its connection to the event and the actions and reactions it has already taken in the event)
3. age: always fill with 30 (a virtual age for institutional accounts)
4. gender: always fill with "other" (institutional accounts use "other" to denote non-individual)
5. mbti: MBTI type used to describe the account's style (e.g., ISTJ represents rigorous and conservative)
6. country: country (in English, such as "China")
7. profession: description of the institution's function
8. interested_topics: an array of focus areas

Important:
- Every field value must be a string or number; null values are not allowed
- persona must be a single coherent text description; do not use newline characters
- {get_language_instruction()} (the gender field MUST use the English value "other")
- age must be the integer 30; gender must be the string "other"
- The institutional account's voice must match its identity"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a basic persona using rules"""

        # Generate different personas based on entity type
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # virtual age for institutional accounts
                "gender": "other",  # institutions use "other"
                "mbti": "ISTJ",  # institutional style: rigorous and conservative
                "country": "China",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }

        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # virtual age for institutional accounts
                "gender": "other",  # institutions use "other"
                "mbti": "ISTJ",  # institutional style: rigorous and conservative
                "country": "China",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }

        else:
            # Default persona
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Set the graph ID used for Zep retrieval"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[Callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        Generate Agent Profiles from entities in batch (supports parallel generation)

        Args:
            entities: List of entities
            use_llm: Whether to use an LLM to generate detailed personas
            progress_callback: Progress callback (current, total, message)
            graph_id: Graph ID used for Zep retrieval to fetch richer context
            parallel_count: Number of parallel workers, default 5
            realtime_output_path: File path for incremental writes (if provided, the file is rewritten each time a profile is generated)
            output_platform: Output platform format ("reddit" or "twitter")

        Returns:
            List of Agent Profiles
        """
        import concurrent.futures
        from threading import Lock

        # Set graph_id for Zep retrieval
        if graph_id:
            self.graph_id = graph_id

        total = len(entities)
        # Pre-allocate to preserve insertion order across parallel workers;
        # Optional[OasisAgentProfile] lets us drop None gaps at return time so
        # the public signature stays a concrete List[OasisAgentProfile].
        profiles: List[Optional[OasisAgentProfile]] = [None] * total
        completed_count = [0]  # use a list so it can be mutated inside the closure
        lock = Lock()

        # Helper for writing files in real time
        def save_profiles_realtime():
            """Persist already-generated profiles to disk in real time"""
            if not realtime_output_path:
                return

            with lock:
                # Filter to profiles that have been generated
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return

                try:
                    if output_platform == "reddit":
                        # Reddit JSON format
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV format
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"Real-time profile save failed: {e}")
        
        # Capture locale before spawning thread pool workers
        current_locale = get_locale()

        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """Worker function that generates a single profile"""
            set_locale(current_locale)
            entity_type = entity.get_entity_type() or "Entity"

            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )

                # Stream the generated persona to the console and the log
                self._print_generated_profile(entity.name, entity_type, profile)

                return idx, profile, None

            except Exception as e:
                logger.error(f"Failed to generate persona for entity {entity.name}: {str(e)}")
                # Create a baseline profile
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)

        logger.info(f"Starting parallel generation of {total} agent personas (parallelism: {parallel_count})...")
        print(f"\n{'='*60}")
        print(f"Starting agent persona generation - {total} entities total, parallelism: {parallel_count}")
        print(f"{'='*60}\n")

        # Execute in parallel via a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Submit all tasks
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }

            # Collect results
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"

                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile

                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]

                    # Write file in real time
                    save_profiles_realtime()

                    if progress_callback:
                        progress_callback(
                            current,
                            total,
                            f"Completed {current}/{total}: {entity.name} ({entity_type})"
                        )

                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} using fallback persona: {error}")
                    else:
                        logger.info(f"[{current}/{total}] Successfully generated persona: {entity.name} ({entity_type})")

                except Exception as e:
                    logger.error(f"Exception while processing entity {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # Write file in real time (even for the fallback persona)
                    save_profiles_realtime()

        # Filter out None placeholders — workers may have failed entirely;
        # callers expect concrete profiles, not gaps.
        concrete_profiles: List[OasisAgentProfile] = [p for p in profiles if p is not None]

        print(f"\n{'='*60}")
        print(f"Persona generation complete! Generated {len(concrete_profiles)} agents in total")
        print(f"{'='*60}\n")

        return concrete_profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Stream the generated persona to the console in full (no truncation)"""
        separator = "-" * 70

        # Build the full output content (no truncation)
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'none'

        output_lines = [
            f"\n{separator}",
            t('progress.profileGenerated', name=entity_name, type=entity_type),
            f"{separator}",
            f"Username: {profile.user_name}",
            f"",
            f"[Bio]",
            f"{profile.bio}",
            f"",
            f"[Detailed Persona]",
            f"{profile.persona}",
            f"",
            f"[Basic Attributes]",
            f"Age: {profile.age} | Gender: {profile.gender} | MBTI: {profile.mbti}",
            f"Profession: {profile.profession} | Country: {profile.country}",
            f"Interested topics: {topics_str}",
            separator
        ]

        output = "\n".join(output_lines)

        # Print only to the console (avoids duplication; logger no longer emits the full content)
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Save profiles to a file, choosing the correct format per platform

        OASIS platform format requirements:
        - Twitter: CSV format
        - Reddit: JSON format

        Args:
            profiles: List of profiles
            file_path: File path
            platform: Platform type ("reddit" or "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save Twitter profiles in CSV format (conforming to the official OASIS spec)

        CSV fields required by OASIS Twitter:
        - user_id: User ID (0-based, derived from CSV order)
        - name: User's real name
        - username: Username in the system
        - user_char: Detailed persona description (injected into the LLM system prompt to drive agent behavior)
        - description: Short public bio (shown on the user's profile page)

        Difference between user_char and description:
        - user_char: Internal use; an LLM system prompt that determines how the agent thinks and acts
        - description: External display; the bio visible to other users
        """
        import csv

        # Ensure the file extension is .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')

        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            # Write the header required by OASIS
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)

            # Write data rows
            for idx, profile in enumerate(profiles):
                # user_char: full persona (bio + persona), used in the LLM system prompt
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # Handle newlines (replace with spaces inside the CSV)
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')

                # description: short bio for external display
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')

                row = [
                    idx,                    # user_id: 0-based sequential ID
                    profile.name,           # name: real name
                    profile.user_name,      # username: username
                    user_char,              # user_char: full persona (used internally by the LLM)
                    description             # description: short bio (external display)
                ]
                writer.writerow(row)

        logger.info(f"Saved {len(profiles)} Twitter profiles to {file_path} (OASIS CSV format)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        Normalize the gender field into the English format required by OASIS

        OASIS requires: male, female, other
        """
        if not gender:
            return "other"

        gender_lower = gender.lower().strip()

        gender_map = {
            "male": "male",
            "female": "female",
            "other": "other",
        }

        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save Reddit profiles in JSON format

        Uses the same format as to_reddit_format() to ensure OASIS can read them correctly.
        The user_id field is mandatory — it is the key OASIS agent_graph.get_agent() uses to match agents!

        Required fields:
        - user_id: User ID (integer, used to match poster_agent_id in initial_posts)
        - username: Username
        - name: Display name
        - bio: Bio
        - persona: Detailed persona
        - age: Age (integer)
        - gender: "male", "female", or "other"
        - mbti: MBTI type
        - country: Country
        """
        data = []
        for idx, profile in enumerate(profiles):
            # Use the same format as to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # Critical: must include user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS-required fields - ensure they all have defaults
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "China",
            }

            # Optional fields
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics

            data.append(item)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved {len(profiles)} Reddit profiles to {file_path} (JSON format, includes user_id field)")
